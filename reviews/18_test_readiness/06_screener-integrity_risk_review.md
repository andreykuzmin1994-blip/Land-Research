# Risk review: screener-integrity (Agent 1, before any code)

Base 29c051e. Track files: arcgis.py, layers.py, checks.py, run.py, report.py,
fulton.py (only if needed), README.md (behavior notes), tests/test_integrity.py.
Reviewed as Tier 2 (STANDING_RISKS.md "Change tiers").

## Evidence gathered (read-only)

- **Zero-match id responses come in two shapes, and both have the key.**
  - The main checkout's cache (`screener/cache/http`, written 2026-10-28) holds 60 `{"objectIdFieldName":"OBJECTID","objectIds":[]}` responses. They are from hosted feature services: NCED, PAD-US and OZ on services.arcgis.com.
  - It also holds 40 `{"objectIdFieldName":"OBJECTID"|"OBJECTID_1","objectIds":null}` responses. They are from on-prem MapServers: Fulton gismaps /33 and /34.
  - One live GET (USFWS wetlands MapServer/0, mid-Atlantic envelope -60,30,-59.999,30.001) returned HTTP 200 with `{"objectIdFieldName":"OBJECTID","objectIds":null}`.
  - So the rule is: **key present means valid. A null or [] value means legitimately empty. A missing key means malformed.**
- **Item b has already happened live.** The cache holds a real 3DEP answer that is HTTP 200 with the body `{"statistics": []}`. It was cached, so a re-run within 7 days gets the same empty answer back.
- **The feature id is not in the returned attributes.** No cached features response contains OBJECTID in its attributes (outFields is restricted), so matching features back to requested ids by id is impossible without changing the request params. A per-chunk **count** check is the practical test.
- **Evaluator baseline**, recorded for the frozen-key gate:
  - `python -m screener.autoresearch.evaluate` gives dev `{"pool":2318,"positives":39,"k":46,"hits":2}`.
  - data/ sha256: features `169b966a…1b54`, labels `b5849f29…c54e`.
  - Screener suite: 23 OK in 0.05 s.
- **Other paths that use these modules.**
  - `build_eval.py` (humans only) imports arcgis, layers, checks and fulton.
  - `evaluate.EVAL_FILES` covers only evaluate.py, data/ and screens.py.
  - `screens.flags()` already turns `slope_error` into "slope check failed for this parcel".
  - The CAMA zip is 63 MB.

## Risks, most severe first

### R1 CRITICAL: a wrong zero-match test stops every run
- **Risk.** If the fix tests truthiness or None (`if not data.get("objectIds")`, `is None`), every legitimately empty tile becomes an error. That stops wetlands (null on the USFWS MapServer), OZ and rail (today's trial area genuinely had 0 of both). For the FID zoning sources, a zoning failure would be reported for every run.
- **Mitigation.**
  - Valid means: the response is a dict, `"objectIds" in data`, and its value is a list or None.
  - Malformed means: the key is missing, or the value is any other type. Raise `ArcGISError`.
  - Also raise on `exceededTransferLimit: true` in an id response.
- **Tests.**
  - The null and [] fixtures (exact shapes above) each give an empty list and no error, through `layers.fetch` and `run.main`.
  - A missing key raises.
  - A non-list value (a string or dict) raises.

### R2 HIGH: the cache stores bad answers, so stops become sticky
- **Risk.** `request_json` caches any HTTP 200 that has no "error" key, for 7 days. That includes:
  - an id answer with the key missing
  - a short feature chunk
  - a 3DEP answer with no histograms (a real one is in the cache today)

  If validation runs only after `request_json` returns, the bad answer is already on disk and every later run reads it back. "Re-run later" then does nothing for up to 7 days, and retries hit the cache instead of the server. The CAMA zip has the same problem for 30 days: `download()` saves any 200 body, such as an HTML maintenance page, as `fulton_cama.zip`.
- **Mitigation.**
  - Validate **before** writing the cache, inside the retry loop. For example, a keyword-only validator argument on `request_json`; it must not become part of the cache key.
  - Apply the same validator to the cache-read path, so an old bad entry is refetched rather than served.
  - In `download()`, check `zipfile.is_zipfile(tmp)` before `tmp.replace(dest)`.
- **Tests.**
  - Pre-seed a temp cache with a malformed entry. The request goes to the network, and the bad file is replaced or not reused.
  - A malformed fresh answer is never written to disk.

### R3 HIGH: checking response shape alone does not fix the observed failure
- **Risk.** The observed failure was *empty-but-valid* flood and wetland answers making a parcel 0%/0%, pass every screen and rank #1. The requirement as written keeps "valid and empty" as legitimate, so that scenario still produces a clean #1 with no note. The fix would give false confidence. The same applies when the exits or industrial layers come back empty: every exit distance is None and every industrial count is 0, with no warning.
- **Mitigation.** Without stopping the run (OZ and rail can legitimately be 0), `run.py` adds a `summary.md` note naming each layer that returned 0 features in the search area. For example: "returned no features near these parcels: … — confirm before relying on the clear result". Keep the per-layer counts in the log.
- **Test.** Valid-empty flood or wetlands gives a successful run *and* a summary note that names the layer.

### R4 HIGH: partial-fetch detection can stop runs falsely, or permanently
- **Risk.** There are four ways this goes wrong:
  - **Stale ids.** A cached id list (7 days) can outlive an upstream overwrite. Hosted views renumber their feature ids when overwritten, so refetching the chunk can never come back complete. A chunk-only retry raises on every run until the cache expires.
  - **Edits between calls.** Live-edited county and city layers can delete rows between the id query and the feature fetch.
  - **Duplicate ids.** Comparing with `len(part)` counts duplicates; compare against `len(set(part))`.
  - **Short servers.** A server whose record limit is below 250 truncates every chunk, flagged by `exceededTransferLimit`. Every cached response today holds 250 features, so this is latent.
- **Mitigation.**
  - Treat a chunk as short when it has fewer features than the unique ids requested, or carries `exceededTransferLimit`.
  - Retry that chunk **without the cache**.
  - If it is still short, re-query the ids without the cache once and refetch.
  - If it is still short after that, raise `ArcGISError` with the counts and the URL, and suggest `--refresh`.
  - Keep retries bounded (SR-11).
- **Tests.**
  - A transient short chunk recovers and the short answer is not cached.
  - A persistent short chunk raises after a bounded number of calls.
  - A stale cached id list recovers through the uncached id re-query.
- **Residual risk.** If an overwrite *reuses* the old ids, the counts match and the wrong features are accepted. Out of scope.

### R5 HIGH: a new exception type would break the existing handlers
- **Risk.**
  - `zoning_checks` keeps going by catching only `ArcGISError`, and `run.py`'s layer stop catches only `ArcGISError`. Any new error that is not an `ArcGISError` (ValueError, KeyError, a custom class) turns a zoning or layer problem into a traceback.
  - A non-dict JSON body (`null`, a list) reaches `.get()` and raises AttributeError today.
- **Mitigation.** Every new validation error is `ArcGISError` or a subclass. In `request_json`, a non-dict body is treated as malformed and retried.
- **Tests.**
  - A malformed id answer from a zoning source gives `zoning_problem` and a summary note, not a crash.
  - A body of JSON `null` or a list raises `ArcGISError`.

### R6 MEDIUM-HIGH: (c) catch too wide or too narrow; `fetch_parcels` left out; corrupt zip stays cached
- **Risk.**
  - `load_cama` can fail with ArcGISError (download), BadZipFile, KeyError (missing member), zlib.error or EOFError (truncated member), csv.Error or OSError.
  - Catching bare `Exception` in run.py hides real bugs as "re-run later", and the operator re-runs forever. Catching only `ArcGISError` misses a corrupt zip.
  - `county.fetch_parcels` (run.py ~79), the first network call, is also unguarded. After R1 and R4 it raises in more situations, but the requirement does not list it.
  - A corrupt cached zip persists for 30 days, so the "re-run later" advice is wrong for it.
- **Mitigation.**
  - Convert zip and CSV read failures at the source into `ArcGISError`, in `fulton.read_cama`; that file is allowed for this. The message names the cached file and `--refresh`.
  - Add the `is_zipfile` check from R2.
  - In run.py, catch `arcgis.ArcGISError` around `fetch_parcels`, `load_cama` and `fetch_industrial_points`, using the existing two-line "! … unavailable" / "Stopping: …" style and `return 1`.
  - Never catch `BaseException` or `KeyboardInterrupt`.
- **Test.** Each of the three calls raising gives exit code 1 with a "Stopping" line and no traceback. No output directory is created. A non-zip cached file gives a clean stop with the `--refresh` hint.

### R7 MEDIUM: slope fix (b) — retry storm, crashes and misleading note text
- **Risk.**
  - **Retry storm.** If an empty 3DEP answer is fed into `request_json`'s 5-try backoff (about 31 s or more), a service that answers 200-but-empty for every parcel costs about 31 s per parcel. Across 6 workers that is hours on a full run.
  - **Crash.** A malformed histogram (no `min` or `max`, or None counts) raises KeyError or TypeError. That goes through `pool.map` and kills the whole run after the slope stage.
  - **Misleading note.** The failure leaves `steep_pct` as None, so the steep screen is *not applied* and topography scores a neutral 0.5. A failed parcel can outrank measured ones. A note that only says "failed" undersells this.
- **Mitigation.**
  - Empty or short histograms give `slope_error` (fixed text such as "no slope data returned"). Do not cache them, and retry at most once.
  - Catch KeyError, TypeError and ValueError from parsing alongside `ArcGISError` in `work()`.
  - Add one summary note with a count, grouped by count only (exception text differs per parcel), e.g. "SLOPE CHECK FAILED for N of M parcels: steep-ground screen not applied to them; re-run later". Emit it only when N > 0.
  - Do not edit screens.py: `flags()` already reports `slope_error`, and screens.py is in `EVAL_FILES`.
- **Tests.**
  - The exact `{"statistics": []}` body gives `slope_error` set, the flag present, the summary count, and at most 2 network calls.
  - A histogram without `min` does not crash the run.
  - No note appears when there are no failures.

### R8 MEDIUM: map.html escaping done in the wrong place
- **Risk.**
  - `html.escape` on the values double-escapes, because the page's `esc()` escapes again. Owner names like "SMITH & SONS LLC" would show as `&amp;`, and "&" is common in owner names.
  - Escaping per field misses fields (`why_site` carries exception text; `flags` carries EPA and EPD site names).
  - Passing `ensure_ascii=False` would let raw U+2028/U+2029 through.
- **Mitigation.**
  - Take the one `json.dumps` result and apply `.replace("<","\\u003c").replace(">","\\u003e").replace("&","\\u0026")`.
  - Keep `ensure_ascii` at its default (True).
  - This is safe because those characters only occur inside JSON strings.
- **Tests.** With owner `</script><script>alert(1)</script><!--` and an address containing `</SCRIPT >`:
  - Exactly 2 case-insensitive `</script` remain: the leaflet include and the closing tag.
  - There is no `<!--` inside the script.
  - The embedded JSON, extracted and passed to `json.loads`, equals the features exactly, including `&` and ` `.

### R9 MEDIUM: brief filename slug (SR-7)
- **Risk.**
  - A slug that collapses runs (`[^…]+`) or lowercases changes today's names (`001_07_030000811192.md`).
  - Dropping the rank prefix allows collisions ("A/B" and "A B").
  - Very long ids exceed the filename length limit (OSError).
  - Blacklisting characters misses `\ : * ? " < > |` and NUL.
- **Mitigation.**
  - Whitelist each character individually: `[A-Za-z0-9._-]`, everything else becomes `_`, with no collapsing.
  - Keep the `NNN_` rank prefix and cap the slug at about 100 characters.
  - Defence in depth: assert that `(briefs/name).resolve().parent == briefs.resolve()`.
- **Tests.**
  - Ids "a/b", "../../x", "..", "a\\b", "x:y", 300 characters, and non-ASCII: every brief lands directly in briefs/, no extra directories, no crash.
  - Ids with only spaces give byte-identical old names.

### R10 MEDIUM: tests could reach the network or leave files behind (SR-6)
- **Risk.**
  - A forgotten stub reaches live servers *and* writes into the worktree's `screener/cache/http`, which is gitignored and so invisible in `git status`.
  - Real backoff sleeps slow the suite.
  - `run.main` sets the global `arcgis.cache_days`, so test order starts to matter.
  - It also creates `screener/inputs/decisions.csv` when that file is missing, which is the case in a fresh worktree.
  - `_session()` is thread-local, so patching one instance misses the slope worker threads.
- **Mitigation.**
  - Patch `arcgis.CACHE_DIR` to a temp directory, and patch `arcgis._session` itself (the module attribute, not an instance) or `requests.Session.post` with a fake that fails on any unexpected call.
  - Patch `screener.arcgis.time.sleep`.
  - Patch `run.DECISIONS_FILE` and pass `--out` as a temp directory.
  - Restore `arcgis.cache_days` with `addCleanup`.
  - Use synthetic owner names only (SR-8).
- **Test.** The suite stays green with the proxy pointed at a dead port, and `git status --ignored screener/` shows no change before versus after.

### R11 MEDIUM: frozen key and the AutoResearch loop
- **Risk.**
  - `build_eval` uses the changed modules. A future key rebuild by a human may now stop where it used to pass. That is desirable, but it should be known.
  - Editing `screens.py` or `data/` would make `--decide` demand a human-recorded new baseline, and editing `rank.py` breaks the loop's ownership of that file.
- **Mitigation.** Do not touch `rank.py`, `screens.py`, `scoring.py`, `settings.toml`, `autoresearch/*` or `data/`. The evaluator output and data hashes must stay unchanged.

### R12 LOW: personal data in errors and notes (SR-8, SR-12)
- **Risk.** An `ArcGISError` that carries a raw response body can embed owner names or addresses (from parcel layer attributes) into `summary.md` notes or `slope_error`.
- **Mitigation.** Messages carry the URL, the missing key name and counts only, with no response bodies. Summary notes use fixed text. The screener has no secrets.

### R13 LOW: politeness and cache keys (SR-11, SR-10)
- **Risk.** Adding request params, such as the id field in `outFields` or `returnCountOnly`, changes every cache key. Operators and build_eval then start from a cold cache (15–25 min plus 63 MB). Extra retries add load on public servers.
- **Mitigation.** Do not change existing query params. Keep all new retries bounded. Make one live trial only. Add no new dependencies.

### R14 LOW: geometry problems that still read as clear
- **Risk.**
  - `layers.fetch` silently drops features whose geometry is null or empty. If a server returns features without usable geometry, the index is empty and parcels read as clear.
  - If `outSR` is ignored, coordinates in the wrong projection read as 0% overlap.
- **Mitigation.**
  - Raise `ArcGISError` when features came back but none produced a shape (`len(feats) > 0 and not items`).
  - Optionally require the response's spatial reference to be 4326. Every cached response has wkid 4326.

## Where the requirement as written is wrong or unsafe

1. **(a)** "No objectIds" must mean *key absent*, not falsy and not None. Null (on-prem MapServer) and [] (hosted feature services) are both normal for zero matches. (R1)
2. **(a, b, c)** Validation must run before caching and must also reject entries already in the cache. Otherwise the stop sticks for 7 days (map layers, slope) or 30 days (the CAMA zip). (R2)
3. **(a)** "After retries" must mean an uncached chunk retry, then an uncached id re-query, then a raise with a `--refresh` hint. A chunk-only retry stops permanently when the cached ids are stale. (R4)
4. **(a)** Checking response shape alone does not close the failure that was observed (valid-but-empty gave a clean #1). Add a non-blocking `summary.md` note for zero-feature layers, plus the R14 "features but no geometry" error. (R3, R14)
5. **(c)** Also guard `fetch_parcels`. Convert zip and CSV errors to `ArcGISError` at the source instead of using a broad except in run.py. Corrupt-cache messages must point at `--refresh` or the cached file, not "re-run later". (R6)
6. **(b)** The note must say what the failure means (the steep screen was not applied) and give N of M. Do not route empty slope answers through the 5-try backoff, and catch histogram parse errors. (R7)
7. **(d)** Escape the serialized JSON with `<`, `>`, `&`. Never `html.escape` the values. Keep `ensure_ascii` at True. (R8)
8. **(e)** Whitelist characters one by one with no collapsing, keep the rank prefix, cap the length, and assert that the file stays inside briefs/. (R9)

## Go / no-go gates for Agent 3

1. `git diff --name-only 29c051e` lists only allowed track files. There is no diff to `rank.py`, `screens.py`, `scoring.py`, `settings.toml`, `owners.py`, `zoning.py`, `autoresearch/*`, `prepare.py`, `parameters.json`, `sources.json` or `program.md`. `screener/requirements.txt` is unchanged.
2. Both suites are green: root 636 OK, and screener 23 plus the new tests OK. The screener suite runs in under about 10 s (no real sleeps).
3. **Hermetic tests.** The suites pass with `HTTPS_PROXY`/`https_proxy` pointed at `http://127.0.0.1:9`, and `git status --ignored --porcelain screener/` is identical before and after the test run.
4. **Zero match (R1).** Tests feed the exact `{"objectIds":null}` and `{"objectIds":[]}` bodies: both are empty with no error. A missing key and a non-list value raise `ArcGISError`. A body of JSON `null` or a list raises `ArcGISError`, not AttributeError.
5. **Cache (R2).** A malformed fresh answer is not written to the cache. A pre-seeded malformed cache entry is not served. `download()` does not cache a non-zip body.
6. **Partial fetch (R4).** A transient short chunk recovers. A persistent short chunk raises `ArcGISError` after a bounded number of calls. The stale-id scenario recovers through the uncached id re-query.
7. **Exception types (R5).** All new failures are `ArcGISError` (or a subclass). A malformed zoning source still produces a `zoning_problem` note, not a traceback.
8. **Clean stops in `run.main` (R6).** A malformed map layer, `fetch_parcels` failure, `load_cama` failure (download error and corrupt zip) and `fetch_industrial_points` failure each give return 1, a "Stopping" message and no traceback, and no out_dir is created. No `except Exception` or `except BaseException` around these calls.
9. **Slope (R7).** The `{"statistics": []}` body gives `slope_error`, the existing flag, and one summary note with "N of M" that states the steep screen was not applied. There are at most 2 HTTP calls per such parcel. A malformed histogram does not crash the run. No note appears when N = 0.
10. **Zero-feature note (R3).** A valid-empty flood layer gives a completed run plus a `summary.md` note naming the layer.
11. **map.html (R8).** With hostile strings, exactly 2 case-insensitive `</script` remain and there is no `<!--` inside the script. The JSON round-trips exactly, including `&` and U+2028.
12. **Briefs (R9).** Traversal and odd ids all land directly in briefs/. Space-only ids keep byte-identical names (`001_07_030000811192.md`).
13. **Frozen key (R11).** data/ sha256 is unchanged (`169b966a…`, `b5849f29…`). `python -m screener.autoresearch.evaluate` still prints dev pool 2318, positives 39, k 46, hits 2.
14. **Live trial.** `python -m screener.run --limit 50 --out <scratch>/out` exits 0. The log shows flood and wetland feature counts above 0, and `summary.md` is written. Afterwards the worktree's `screener/cache/`, `screener/output/` and `screener/inputs/decisions.csv` (if the trial created it) are removed, and `git status --ignored` shows nothing new. It runs once only (SR-11).
15. **Docs and commits.** The README change is limited to behavior notes and is accurate. No model names appear in code, comments, docs or commits. Nothing is pushed and no temporary branches remain.

## Out of scope (record as follow-ups)

- CSV formula injection in `ranked.csv`/`rejected.csv` (owner names starting with `= + - @`).
- Markdown or HTML from county strings in briefs and summary (and `|` breaking the summary table).
- Feature ids reused after an upstream overwrite; the count check cannot see this.
- Failed slope scoring as neutral (0.5) and skipping the steep screen. That is a policy choice in human-owned `scoring.py`/`screens.py`; `screens.py` is in `EVAL_FILES`.
- A human may want to rebuild the frozen key with the stricter client and compare fingerprints. A difference would reveal a past silent partial fetch in the key.
- Stale briefs left over when an `--out` directory is reused. Output writes are not atomic (SR-7's atomic-write half).
- Slope runs 6 parallel workers against USGS with no Retry-After handling (SR-11; this predates the track).
- A corrupt cache JSON file on the read path raises an uncaught JSONDecodeError (unlikely, because cache writes are atomic). It can be folded into the R2 validator cheaply.
