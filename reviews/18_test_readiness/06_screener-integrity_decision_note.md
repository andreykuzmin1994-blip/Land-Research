# Decision Note — Screener integrity (silent-zero layers, slope failures, clean stops, map/brief escaping)

> Screener track (`screener/`, governed by `screener/README.md`), reviewed
> as Tier 2 per `STANDING_RISKS.md` § "Change tiers": silent bad data
> misleads land buyers. Between runs; no AutoResearch run active.
> Risk review: `06_screener-integrity_risk_review.md` (R1–R14).

- **Roles**: risk reviewer (Agent 1, before any code), implementer
  (Agent 2, against Agent 1's gates) and adversarial reviewer/committer
  (Agent 3). Each role ran in an independent fresh context on the
  session's strongest available model. No role reviewed its own work.
- **Verdict**: APPROVE. Agent 3 made two small fixups (below).

## What changed

1. **(a) Silent-zero and partial layers** (`arcgis.py`, `layers.py`).
   `request_json` takes a keyword-only `check`. A failing answer is asked
   for at most twice, raises `BadAnswer` (an `ArcGISError`), and is never
   cached or served from the cache. An id answer is valid when
   `objectIds` is present and is a list or null. Both live zero-match
   shapes (`[]` hosted, `null` MapServer) are normal. A feature chunk is
   short when it has fewer features than unique ids, or carries
   `exceededTransferLimit`. A short layer retries once with an uncached
   id list (stale ids after a re-publish), then stops with the counts
   and `--refresh`. Features must come back in WGS84, and a layer whose
   features all lack geometry stops the run. Non-object JSON bodies are
   `ArcGISError`, not `AttributeError`. Query params and cache keys are
   unchanged (pinned by a test).
2. **Zero-feature note** (`run.py`). Layers with no features near the
   parcels (and an empty industrial list) are named in `summary.md`
   without stopping the run (OZ and rail can legitimately be 0).
3. **(b) Slope** (`checks.py`, `run.py`). A 3DEP answer without usable
   histograms (`{"statistics": []}`, missing `min`, null counts, zero
   pixels) is a `BadAnswer`: at most 2 calls, not cached, and the parcel
   gets `slope_error` (the existing flag). `summary.md` gets
   "SLOPE CHECK FAILED for N of M parcels: the steep-ground screen was not
   applied …" only when N > 0.
4. **(c) Clean stops** (`run.py`, `fulton.py`, `arcgis.download`).
   `fetch_parcels`, `load_cama` and `fetch_industrial_points` failures
   use the existing "! … unavailable" / "Stopping:" lines and return 1
   (catching `ArcGISError` only). `download()` refuses a non-zip body or
   cached file. `read_cama` turns zip and CSV read errors into
   `ArcGISError` naming the file and `--refresh`.
5. **(d) map.html**: the single `json.dumps` result has `<`, `>` and `&`
   replaced with `\u` escapes. `ensure_ascii` stays at True, and no
   values are `html.escape`d.
6. **(e) Briefs**: a per-character `[A-Za-z0-9._-]` whitelist slug,
   capped at 100 characters, with the `NNN_` prefix kept and a check
   that the file stays in `briefs/`. Names for space-only ids are
   byte-identical.
7. `screener/README.md` notes this behaviour. 33 new offline tests are in
   `screener/tests/test_integrity.py`.

## What was verified (independently by Agent 3)

- **Tests fail on base**: base versions of the six library files with
  the new test file gave 20 failures and 16 errors in 33 tests. Only the
  regression guards passed (both zero-match shapes, cache-key pinning,
  duplicate ids, a clean run, a good slope answer).
- **SR-6**: screener suite 56 OK (23 + 33) in about 0.2 s, root suite
  636 OK. Both ran with `HTTPS_PROXY`/`https_proxy` pointed at a dead
  port. `git status --ignored` was identical before and after the
  screener suite, and the tests have no real sleeps.
- **No false stops on real data**: an offline scan of the main checkout's
  cache found 209 of 209 geometry answers with wkid/latestWkid 4326. It
  found 100 zero-match id answers (60 `[]`, 40 `null`), no id answer
  without `objectIds`, and no `exceededTransferLimit`. The one cached
  `{"statistics": []}` 3DEP answer is now refetched (offline probe).
- **Live trial (once, SR-11)**: `python -m screener.run --limit 50` on a
  cold cache exited 0. Features came back: flood 19, wetlands 157,
  PAD-US 2, EPA 25, HSI 11, exits 12, rail 135 (NCED and OZ 0, named in
  the new note). All 30 slope checks passed. South Fulton zoning and FLU
  (hosted join, hosted overlay, on-prem FID /33 and /34) came back
  without false failures. map.html had 2 `</script`, and its JSON
  matched `ranked.geojson`. Brief names kept the old format. Afterwards
  the worktree cache and the trial's `decisions.csv` were removed, and
  the owner-bearing outputs were deleted from scratch.
- **Offline probes**: `--refresh` mode recovers from a short chunk. An
  HTTP 500 followed by two bad answers stops after 3 calls.
- **SR-7** briefs: traversal, `\`, `:`, NUL, 300-character and non-ASCII
  ids all land in `briefs/`.
- **SR-8**: errors and notes carry URLs, counts and fixed text only.
  Tests assert that synthetic owner names are absent.
- **SR-1, SR-10, SR-12**: the diff touches only allowed track files. No
  immutable or human-only file changed, no new dependencies, and no
  secret-shaped literals or model names.
- **Frozen key**: data sha256 unchanged (`169b966a…1b54`,
  `b5849f29…c54e`). The evaluator still prints dev pool 2318,
  positives 39, k 46, hits 2.

## Risks Agent 3 found, and what changed

- **Sticky null-geometry stop (fixed)**. An answer whose features all
  lack geometry passes the pre-cache checks and is cached. The R14 stop
  therefore repeats for 7 days, but the message said only "re-run
  later" and the README said bad answers are never cached (confirmed by
  an offline probe). Agent 3 fixups: the `layers.fetch` message now says
  "re-run with --refresh", the README sentence is accurate, and the test
  asserts the hint. Moving this check before the cache write, per chunk,
  was rejected. It would falsely stop on chunks of geometry-less parcel
  rows, which `fetch_parcels` already skips.
- **Accepted by design**: a server capping answers below 250 records
  now stops the run instead of silently dropping rows (none was seen
  live or in the cache). 3DEP answers with zero pixels now count as
  failed slope checks.
- **Cosmetic**: "Stopping: re-run later" is printed even when the detail
  line says to use `--refresh` or delete the file.

## Deviations

`fulton.py` changed (allowed "only if needed": zip validation and error
conversion). Some tests use private helpers (`_cache_path`,
`_envelope_params`, `_map_html`) to pin cache keys and page output,
which was accepted.

## Residual gaps / human follow-ups

- A damaged CAMA zip with an intact directory stays cached. The stop
  message says to delete it or use `--refresh`.
- `build_eval.py` (human-only) now uses the stricter client. A key
  rebuild may stop where it used to pass silently. Comparing
  fingerprints would reveal any past partial fetch.
- A failed slope check still scores neutral topography and skips the
  steep screen. That policy lives in human-owned `scoring.py` and
  `screens.py`.
- From the risk review's out-of-scope list: CSV formula injection,
  Markdown from county strings in briefs and summary, feature-id reuse
  after an overwrite, stale briefs in a reused `--out`, non-atomic
  output writes, and slope workers without Retry-After.
- The root suite writes `sources/disco-fulton-*` into the repo root on
  every run. This predates the track and is outside it.
