# Zoning-code scraper — design

> Tier 2 (external-service integration, a new human-owned file and a new
> runner check). The operator approved the design below; the pre-change
> risk register (RS-01..RS-30) came from an independent reviewer and its
> disposition is `02_risk_review.md`. Model names are omitted, as in
> `reviews/18_test_readiness/`.

## Why

`zoning_rules.json` (reviews/21_zoning) was drafted by hand from a
third-party mirror and a few official PDFs. Every jurisdiction is
`draft`. A human who wants to flip one to `verified` needs to know, per
value, what the sources say today, which source said it, and whether two
"agreeing" sources are really independent. This tool produces that
evidence. It does not decide anything.

## Pieces

| Piece | Status | What it does |
|---|---|---|
| `rules_sources.json` | new, HUMAN-OWNED | Per jurisdiction: sources (url, `html`/`pdf`, `official`/`mirror`, parser, sections cut by explicit start/end markers, parser config), GIS change-signal layers, conditions-suffix convention; per host: terms review fields. Strict validation, SHA-256 freeze, git-HEAD check in the runner. |
| `rules_scrape.py` | new, protected, between runs | Fetch -> cache -> cut sections -> extract -> **verify** -> candidate + diff; `--watch`, `--flags`, `--offline`, `--assisted`, `--llm`. Imports `prepare`, `zoning`, `zoning_import` only. |
| `runner.py` | protected, between-runs edit | `verify_rules_sources_committed()` (git blob check, in runner so runner never imports the scraper); `verify_setup` sub-check `rules_sources`; `evaluate` backstop after the prototypes check. |
| `Makefile` | ops | `make rules-scrape`, `make rules-watch`. |
| `.gitignore` | config | `rules_cache/` (raw pages, metadata, cached extractor replies), `rules_out/` (candidate, diff, watch state, flags). |
| `research.py`, `zoning.py`, `site_fit.py` | **unchanged** | `git diff 0418d2c -- research.py site_fit.py zoning.py` is empty; both golden checks are identical. |

## Data flow

1. `PoliteFetcher.fetch(url)`: https only; hard-coded denylist
   (`municode` anywhere in the host, `mcclibrary`, the Azure Gov blob
   host); robots.txt parsed by an RFC 9309 matcher in the tool (longest
   match, `*`, `$`, Allow wins a tie) and evaluated for the tool's token
   AND ClaudeBot, Claude-User, Claude-SearchBot, anthropic-ai, Claude-Web
   (most restrictive wins); Content-Signal `ai-input=no` refuses;
   robots 404/410 allow, anything else that is not a clean text 2xx
   (401/403, challenge, 5xx, timeout, non-text, off-site redirect) denies
   the host; Crawl-delay above 1 s is honoured, above 30 s denies. One
   fixed User-Agent (`Land-Research-RulesScrape/1.0 (...)`, no browser
   prefix, no e-mail), no cookies, `allow_redirects=False` with every hop
   re-checked (denylist, https, same registrable domain, robots), 1 req/s
   per host, retries only for 5xx/connection errors, 429 only with a
   Retry-After <= 30 s, a request budget per invocation (default 150,
   exit 5). 401/403/407, `cf-mitigated`/`cf-chl*` headers, Akamai or
   Cloudflare 403 and challenge markers in a 200 body are "unreachable":
   one request, no retry, nothing cached. Subresources are never fetched.
2. `Cache`: `rules_cache/<sha256(url)>.body` + `.meta.json` (url, final
   url, status, content type, fetched_at, sha256, size, tool version,
   robots verdict, pdftotext version and argv). Atomic writes through the
   protected-path guard. Replay re-hashes and refuses a changed entry.
3. Documents: HTML through `html.parser` with script/style/noscript/
   template/head/svg/nav/footer, `hidden`, `aria-hidden` and
   `display:none` content dropped; tables kept as rows (`a | b | c`),
   `<sup>x</sup>` rendered `[^x]` so footnote markers survive. PDF through
   `pdftotext -layout` when installed (raw layout lines kept for the
   aligned-grid parser), else "pdf text unavailable". Sections: the first
   start marker (longest span, or the last occurrence), optional further
   start markers in order, then the end marker; a missing marker is "not
   found in source", never "removed".
4. Extraction: `district_grid` (South Fulton Table 4-1, Hapeville chart;
   colspan/rowspan expanded, header paths matched to registry columns,
   any district row whose width differs from the header makes the table
   needs_review with no values), `label_value` (Fairburn two-column
   tables, Fulton County and Union City numbered lists, East Point
   "label—value" lines), `use_table` (P/S/A/blank legends from the
   registry), `pdf_grid` (values only when every token sits in exactly one
   header column's span), `llm_prose` (the optional CLI extractor or an
   `--assisted` records file; otherwise "needs extraction").
5. `verify()` — one function for every extractor (deterministic,
   assisted, llm): quote <= 300 characters, no control characters,
   verbatim in the cut section after whitespace collapse ONLY; header or
   label parts verbatim too; the field keyword in the header/label (tables)
   or in the quote (prose) and, for prose, the nearest field keyword before
   the number must be this field's; the district named in the row label or
   quote, or the section registered as that district's; another district
   named without this one -> rejected; several districts named without a
   shared-row flag -> needs_review; the number as a token that is not a
   section/ordinance/table/footnote number, next to the field's unit
   (ft, sq ft, acres -> sq ft by explicit logged conversion, %, "times"
   for FAR) or, for deterministic table cells only, under a column whose
   registry unit or header gives the unit; pct in (0, 100]; words for
   numbers, "None" read as 0, unit conversions by a non-deterministic
   extractor, several values in one cell and every qualifier (footnote
   markers, whichever is greater, except, unless, provided that, see Sec.,
   subject to, adjacent/abutting, within N feet of, special use, overlay,
   if/when/where, Note:, Amended) -> needs_review. Confidence: official +
   deterministic = high; official or deterministic = medium; mirror
   assisted/llm = low; any review item costs a level. Mirror values are
   never high.
6. Candidate (`rules_out/zoning_rules.CANDIDATE.json`): the
   zoning_rules.json schema plus a `_candidate` block (marker, tool
   version, registry and current-rules SHA-256, per source status/sha/
   fetched_at, per section sha, unplaced values). Jurisdiction metadata
   (`name`, `unlisted_districts`, `governing_jurisdiction`,
   `district_conversions`, `unincorporated_parcel_ids`) is copied from the
   current rules, never inferred; every status `draft`, `verified_by` /
   `verified_on` null (runtime assertion, exit 1 otherwise); a district
   without an extracted use status keeps the current one marked
   `copied_from`; values for districts not in the current rules go to
   `_candidate.unplaced_values`. `zoning.validate_rules` passes.
7. Diff (`rules_diff_report.md` / `.json`): per (jurisdiction, district,
   field): same; same (mirror vs mirror: not independent); same value,
   new official cite; changed; added; not comparable (current value is
   text); source unreachable; source disabled; not found in source; pdf
   text unavailable; needs extraction; not extracted; not covered by a
   registered source; district in GIS but not in rules (with `--flags`).
   Counts both per district and per (source row, field), so a merged
   M-1/M-2 row counts once.
8. Watch (`--watch`): per section text hash, raw bytes (low weight), the
   newest "Ord. No." date on the page (with age; over 365 days flagged)
   next to the current rules' `as_of`, GIS `editingInfo.lastEditDate` plus
   feature count and max object id; each changed / unchanged / unknown;
   GIS skipped while the loop holds its flock. State in
   `rules_out/watch_state.json`. Never applies anything.
9. Flags (`--flags`): run-state guard reused from zoning_import
   (`tsv_has_baseline`, `mirror_blocks`, same `--no-open-run-check`
   semantics, exit 3); lazy, read-only connection (`SET TRANSACTION READ
   ONLY` first); `SELECT parcel_id, zoning, zoning_description, extents`;
   suffix regex per jurisdiction from the registry; case polygons with
   outFields = case id + status only, intersected by
   `ST_Intersects(geometry, ST_GeomFromGeoJSON(%s))`; only registry
   `approved_statuses` count as "conditions on file", other statuses go to
   "other cases". Console: counts. Report: parcel ids and public case ids
   only, `rules_out/conditions_flags.json`.

## LLM extractor (optional)

`claude -p --tools "" --bare --strict-mcp-config --mcp-config <empty>
--setting-sources "" --no-session-persistence --output-format json
--json-schema <fixed> --system-prompt <fixed>`, argv as a list
(`shell=False`), cwd and HOME a fresh temp dir outside the repo, an
allow-listed environment, the section on stdin between delimiters, a
timeout and a size cap. The reply is parsed strictly (exact key set,
types, field and unit enums, districts of the section); the tool, not the
model, sets cite, source class and extractor; `verify()` then runs. Replies
are cached under `rules_cache/llm/` keyed by (section sha, prompt sha,
schema sha); `--offline` uses the cache only. Here the CLI answers "Not
logged in" under `--bare` (it accepts only `ANTHROPIC_API_KEY`), so the
first candidate used the assisted path (`assisted_records.json`).

## Operator prerequisites

`pdftotext` (poppler-utils) for PDF sources. No Python package was added;
`requirements.txt` is unchanged (SR-10).
