# 03 — Zoning-code scraper: decision note

> **Tier 2.** Design `00_design.md`; pre-change register and dispositions
> `02_risk_review.md`. This note records the first candidate, the
> independent **adversarial** post-change review (three fresh contexts
> that did not write the code: integrity, etiquette, correctness; every
> finding was challenged by a separate skeptic pass, and probed with
> repro scripts, a live local PostGIS and live GIS requests, counts
> only), and what changed after it. Model names are omitted, as in
> `reviews/21_zoning/`.

## Change

- **`rules_scrape.py`** (new, protected, between runs only; `make
  rules-scrape`, `make rules-watch`). Fetches the sources registered in
  `rules_sources.json` politely (Municode denylist, robots.txt for its own
  and the AI-agent tokens, 1 req/s per host, challenge pages
  unreachable), caches raw bytes in the gitignored `rules_cache/`, cuts
  sections, extracts values (deterministic parsers; `--llm` or an
  `--assisted` records file for prose), sends every value through one
  verifier, writes a candidate in the `zoning_rules.json` schema (every
  jurisdiction `draft`) and a diff report under the gitignored
  `rules_out/`. `--watch` compares section hashes, the newest "Ord. No."
  date and GIS layer edits; `--flags` (read-only database) lists parcels
  with conditions of zoning on file. It never writes `zoning_rules.json`
  and imports neither `research` nor `runner`.
- **`rules_sources.json`** (new, human-owned). Frozen by SHA-256 in the
  tool, checked against git HEAD by `runner.verify_setup` and
  `runner.evaluate`, on every protected-file list.
- `research.py`, `site_fit.py`, `zoning.py`: unchanged (`git diff 0418d2c`
  is empty). No schema change.

## Review verdict: approve-with-fixes (5 high, 13 should-fix, 5 nits; 21 fixed, 2 left to the operator)

| # | Severity | Finding | Resolution |
|---|----------|---------|------------|
| 1 | high | A marker chain ('Sec. 10-2080 ... I-2', then 'Development standards.') searched each later marker with no bound. When the I-2 block had no such heading, the cut slid into the I-3 block, and the district-scoped verifier accepted I-3's 150 ft rear yard as I-2, at high confidence. | `cut_section` steps to a later marker only when the gap holds no line-start `Sec. N` heading, not the section's own end marker and no other registered section's first marker (`_chain_gap_ok`). Otherwise the section is "not found in source", which now exits 4. Tests `test_chain_does_not_jump_to_the_next_block` (the reviewer's East Point shape) and `test_chain_stops_at_its_own_end_marker`. All 15 live sections still cut to byte-identical hashes. |
| 2 | high | Prose verifier: `section.norm` joined a table header and its data row ('... Rear yard M-1 \| 60 feet ...'), so rear=60 (the front value) was accepted. '20 feet side yard; 50 feet rear yard' gave side=50, because only keywords before the number were read. | A non-deterministic quote must lie on one line of the source. Across table rows it is rejected; across other lines (layout grids) it goes to needs_review. `_attribution`: a field keyword right after the number decides ('50 feet rear yard'). Otherwise the nearest labelled keyword before it ('Rear:') decides, and another field's unlabelled wording between that label and the number sends the value to review. Tests `test_quote_across_table_rows_rejected`, `test_quote_across_plain_lines_needs_review`, `test_postfix_label_decides`. |
| 3 | should-fix | `district_re` let 'M-1-C' match M-1, so a conditional-zoning row became the base district's value at high confidence. | Lookahead `(?![A-Za-z0-9]\|-[A-Za-z0-9])` in `district_re` and in the pdf_grid row match. A grid or PDF row carrying only a suffixed variant gives a section-level needs_review. In the verifier, a quote naming only a suffixed variant is rejected; one naming both goes to review. Tests `test_district_re_excludes_conditional_suffix`, `test_grid_row_with_suffix_is_review_not_a_value`, `test_assisted_quote_naming_only_the_suffixed_district_rejected`. |
| 4 | should-fix | Soft hyphen / zero-width characters split a number: the cell '4&shy;0'' was read as 4. | `sanitize` drops every Unicode Cf character, so HTML text, PDF text and cells read '40'. A quote that still carries one is rejected as "control characters". Test `test_soft_hyphen_and_zero_width_are_dropped`. |
| 5 | should-fix | No qualifier for limiting wording; Union City lot width and frontage 150 "for initial zoning request" were high/accepted. | New qualifiers: limited_scope ("for initial/new/existing/corner ..."), adjustable ("may be reduced/increased/waived ..."), variance, up_to (ignored for max_* fields, where it restates the maximum), numeric ranges with a unit, per_unit, per_conditions. Union City width/frontage are now medium + needs_review. Test `test_limiting_wording_qualifies`. |
| 6 | should-fix | A mirror host relabelled 'official' in the registry got high confidence and lost `mirror_only`. The scraper did not compare the registry with git HEAD. | `validate_registry` refuses (exit 1) a source on a `MIRROR_HOSTS` host unless its class is `mirror`. `registry_matches_head` (git blob comparison, local so that runner is never imported) prints a counts-only warning when the default registry differs from HEAD. Tests `test_mirror_host_must_be_mirror` (incl. the live registry relabelled), `test_registry_head_check`. |
| 7 | high | A robots.txt with a UTF-8 BOM failed open: the first group was dropped. | Decoded with `utf-8-sig`, and `Robots` strips a leading U+FEFF. Test `test_robots_bom_does_not_hide_the_first_group` (parser and fetcher). |
| 8 | should-fix | 5xx with Retry-After was retried after a fixed 2 s / 4 s. | Every 5xx reads Retry-After like 429: above 30 s it is unreachable with no retry; otherwise the tool sleeps at least that long. Test `test_503_retry_after_honoured_or_unreachable`. |
| 9 | should-fix | The robots matcher did not normalise percent-encoding (fail-open). | `pct_normalise` on pattern and path (unreserved characters decoded, other escapes upper-cased). Test `test_robots_percent_encoding_normalised`. |
| 10 | should-fix | Incapsula, DDoS-Guard, AWS WAF (202), Sucuri interstitials were cached as pages. | More title/body markers (also PerimeterX, DataDome), `x-amzn-waf-action` / `x-sucuri-block` headers, and any 202 with an HTML body are unreachable. Test `test_more_challenge_pages_unreachable`. |
| 11 | should-fix | `--flags --report reviews/...` put parcel ids into a tracked folder. | `guard_private_path`: with `--flags`, the report folder must be outside the repository or under `rules_out/`/`rules_cache/`. Checked before any network or database work. Test `test_flags_report_in_a_tracked_folder_refused_before_any_work`. Live: the documented review-artifact command with `--report reviews/23_rules_scraper/x.md --flags` was refused with exit 1. |
| 12 | should-fix | The first live run used `--allow-unreviewed-terms`; no host's terms are reviewed. | **Operator action, not a code change.** README step 1 now says a human reviews each host's terms first, and the flag is a one-off exception, never a default. The Makefile does not set it. The regenerated candidate below was built offline from the first run's cache, with no new fetch of any code page. |
| 13 | high | Quote check was a plain substring ('5 feet; side yard' cut from '25 feet; side yard'), and a missing preceding keyword passed. | `_bounded_find`: a quote must start and end on token boundaries. `_attribution` rejects a number with no field wording before it or right after it. Test `test_fragment_of_a_number_rejected` (both repros, plus 'Rear: 50 feet maximum. Height' for height). |
| 14 | high | A prose null was accepted when any 'None'/'N/A' was anywhere in the quote. | `_null_attached`: the phrase must follow this field's wording with no other field's wording and no number in this field's unit in between ('Side: None'), or open the clause naming the field ('No fixed minimum lot widths or areas'). The same rule applies to 0 read from 'None'. Test `test_null_must_be_attached_to_the_field`. The 6 assisted Atlanta nulls still pass. |
| 15 | should-fix | Qualifiers were read only inside the quote; Fairburn's 'Note: ... abutting a residential district must maintain a side and rear yard of 50 feet' never surfaced. | Prose: the sentence(s) around every place the quote occurs, within its source line, are scanned too. All records: 'Note:' lines of the section that use the field's wording (and do not name only other districts) add a needs_review. Fairburn M-1/M-2 side and rear are now low + needs_review. Test `test_sentence_and_note_qualifiers_surface`. |
| 16 | should-fix | The watch rebuilt its state from scratch, so baselines were lost (offline, `--jurisdiction`, unreachable). | `watch_compare` starts from the previous state and replaces an entry only after a fresh, successful observation. Test `test_watch_keeps_unobserved_baselines` (offline GIS, jurisdiction-limited, then unreachable source, then a real change still "changed"). |
| 17 | should-fix | A section that disappeared showed as "unknown", exit 0. | A reachable page without the section stores `missing`: it is reported "changed" with "section lost ...", named on the console, and exits 4. Any enabled section not found in a reachable source exits 4. Test `test_lost_section_is_changed_and_exit_4`. |
| 18 | should-fix | Case hits were a bare `ST_Intersects`, so sliver overlaps flagged neighbours. | `_SQL_CASE_HITS` returns the overlap share of the parcel and of the case polygon (`ST_MakeValid`, module-level SQL, `%s` only). A hit counts when either share is at least `MIN_CASE_OVERLAP` = 5%. Smaller hits go to `adjacent_case_low_overlap`, with the share recorded. Test `test_low_overlap_case_listed_separately`; live numbers below. |
| 19 | nit | The output guard protected a fixed name list only; derived paths were checked late. | Inside the repository, writes go only under `rules_out/`, `rules_cache/` and `reviews/23_rules_scraper/` (refuses `experiment_log.tsv`, `.env`, `screener/`, tests, golden records, any other tracked file). The report's `.json` sibling, `watch_state.json` and `conditions_flags.json` are checked before any work. Test `test_repo_writes_only_into_the_tools_folders`. |
| 20 | nit | Versioned agent tokens ('ClaudeBot/1.0') and a Content-Signal before any group were ignored. | Product token compared case-insensitively. A Content-Signal outside a group applies to every group. Test `test_robots_versioned_agent_and_global_content_signal`. |
| 21 | nit | `--jurisdiction` reported the other jurisdictions as "not covered", and `--flags` ignored it. | `diff(..., jurisdictions=)` and `compute_flags(..., jurisdictions=)` scope to the selection. Test `test_diff_scope_and_categories`. Live: an Atlanta-only run listed only Atlanta rows. |
| 22 | nit | The FID M-1A lot coverage (70%) and the heights are in the cut section but not in the registry labels. | **Operator action** (human registry edit, between runs). A coverage label is needed, and the value wraps to the next PDF line, so label_value would need multi-line support or the section goes to assisted/llm. Heights stay text ("not comparable" is expected). Not done: the agent does not edit `rules_sources.json`. |
| 23 | nit | Diff categories only partly matched the spec. | "not extracted" is now "removed (section read, value not extracted)". "same value, new cite" covers a different official document URL. `conditions_suffix` also uses `zoning.normalize` ('<digit>C'; still only for jurisdictions with a registered regex). Copied use statuses carry `source_class` (cite class) and `quote: null`. The candidate `_comment` says it is a per-value draft, not a drop-in replacement. Test `test_diff_scope_and_categories`. |

No finding was refuted. The skeptics confirmed every repro against the
pre-fix code. Finding 12 is an operator gate, and finding 22 is a human
registry edit.

## What the reviewers verified

- The repro scripts (`cut.py`, `attack.py`, `grid.py`, `robots_probe.py`,
  `retry_probe.py`, `vprobe.py`, `flagprobe.py`) against the pre-fix
  code. All reproduced.
- The live candidate: Union City width/frontage 'for initial' at high,
  and the Fairburn note next to accepted side/rear values.
- The live `--flags` numbers on a scratch copy of the local database (17
  requests to gis.atlantaga.gov).
- The standing risks: SR-1 (no write to a human-owned file; the guard is
  now stricter), SR-2 (the new SQL is a module-level constant with `%s`;
  the static test still passes), SR-6 (all new tests are offline), SR-7
  (atomic writes), SR-10 (`requirements.txt` unchanged), SR-11 (5xx
  Retry-After, now fixed).

## Evidence

- Suites: `python -m unittest discover -s tests`: **1287 OK** (was 1261;
  `test_rules_scrape.py` 122, was 96).
  `python -m unittest discover -s screener/tests -t .`: **134 OK**.
- `tests/zoning_golden_check.py`: identical to the ba4a780 record.
  `tests/site_fit_golden_check.py`: identical to the 28d3205 record (also
  with south_fulton verified). `git diff 0418d2c -- research.py
  site_fit.py zoning.py` is empty.
- **Regenerated candidate** `04_candidate_round1.json` and
  `04_candidate_round1_diff.md` / `.json`. Built offline from the cached
  bytes of the first live run: the source and section SHA-256s are
  identical to `01_first_candidate.json`, and no code page was fetched
  again. Assisted records are unchanged. `--flags` ran against a local
  database. Verifier: **58 accepted, 40 needs_review, 0 rejected** (was
  66 / 32 / 0), 2 section-level needs_review, 15 of 15 sections found.
  103 entries, as before. **No value changed.** 12 entries changed review
  state or confidence: Atlanta 6 (quotes spanning lines; side yards with
  front-yard wording between label and number), Fairburn 4 (the
  abutting-residential note), Union City 2 (width and frontage 'for
  initial zoning request'). By confidence and review: high 11 (was 13),
  medium 39 + 11 review (was 43 + 9), low 6 + 28 review (was 8 + 22).
- **Agreement with `zoning_rules.json`** is unchanged from the first
  candidate (district x field, all jurisdictions): same 20 (official,
  independent: Union City 8, FID 12), same mirror vs mirror 44 (NOT
  independent: South Fulton 18, Fairburn 14, Atlanta 12), changed 0,
  added 29 (9 in the five corridor jurisdictions), not comparable 2
  (South Fulton M-1/M-2 height text), removed (section read, value not
  extracted) 4 (Atlanta I-1/I-2 rear yard and lot frontage: the code
  states none and the current value is null too), not covered by a
  registered source 27, district in GIS but not in rules 70. Every
  disagreement and open item listed under "First live candidate" below
  still stands. In addition, Fairburn side/rear now carry the note's 50 ft
  abutting-residential rule as needs_review.
- **Live flags (F18)**, Atlanta only, `rs23_fix_*` scratch database
  (`createdb -T zoningcheck`, since dropped), 17 requests to
  gis.atlantaga.gov: before the fix 206 case hits on 335 Atlanta parcels
  all counted. Now 82 parcels with conditions on file (110 hits: 23 `-C`
  districts + 87 Complete cases), 21 parcels with other cases (23 hits:
  Correction 13, Filed 6, Pending 2, Denied 2), and 64 parcels with 96
  hits under 5% overlap, listed apart (67 Complete). 33 parcels had only
  low-overlap hits and are no longer flagged. The first run counted 113
  parcels with conditions on file.
- Live guard: `--flags --report reviews/23_rules_scraper/x.md` was
  refused (exit 1) before any request.

## Behaviour a human will notice

- More values are needs_review and fewer are high: quotes spanning source
  lines, section notes, limiting wording, wording between label and
  number. None is silently resolved.
- Exit 4 also when an enabled section is not found in a reachable source
  (markers broken by a page change). The watch names "section lost"
  sections.
- `conditions_flags.json` has a new `adjacent_case_low_overlap` list and
  an `overlap_pct_of_parcel` per hit. The console adds the low-overlap
  count.
- With `--flags`, `--report` must be outside the repository or under
  `rules_out/`. Any other repository path except the tool's three folders
  is refused.
- A warning line when `rules_sources.json` differs from git HEAD.
- The watch state survives offline, `--jurisdiction` and unreachable
  runs. A state written by the earlier code may hold nulls; they are
  "unknown" once and are replaced by the next good observation.

## What the operator must do

1. Read the terms of www.zoneomics.com (in a browser: the page renders
   client-side), www.fultoncountyga.gov, www.unioncityga.gov,
   gis.atlantaga.gov and the ArcGIS hosts. Fill `terms_reviewed_by` /
   `terms_reviewed_on` in `rules_sources.json` and commit. Until then,
   `--allow-unreviewed-terms` is a one-off exception (finding 12).
2. Optional registry edit (finding 22): an FID M-1A lot-coverage entry,
   which needs multi-line label support or the assisted/llm path.
3. Before verifying any jurisdiction: read every needs_review item in
   `04_candidate_round1_diff.md`. Above all, Fairburn's 50 ft
   abutting-residential side/rear, Union City's 'for initial zoning
   request', and South Fulton footnote 19.
4. Decide whether the 5% case-overlap threshold suits you
   (`MIN_CASE_OVERLAP`, a code constant). Check the low-overlap list by
   hand where a parcel's boundary is doubtful.
5. PR #34 now also carries this scraper and this review round. Reviewers
   of #34 review it too, or it is split out.

## First live candidate (2026-11-05, before the review)

Run from a throwaway clone with `--allow-unreviewed-terms --assisted
reviews/23_rules_scraper/assisted_records.json --flags --local-db-only
--no-open-run-check`. Artifacts: `01_first_candidate.json` and
`01_first_candidate_diff.md` / `.json`. 10 sources registered, 9 enabled
and fetched, College Park disabled (markers unverified). Requests:
www.zoneomics.com 8, www.fultoncountyga.gov 2, www.unioncityga.gov 2;
Municode never contacted. 98 records (deterministic 86, assisted 12, llm
0): 66 accepted, 32 needs_review, 0 rejected.

Implementation decisions: the runner checks `rules_sources.json` against
HEAD (RS-08). Loop detection probes the flock, not the lock file. Union
City is read deterministically (Sec. 6-13 D label list; registry edit
committed separately). The Atlanta prose went through the assisted path:
the CLI extractor is wired and tested with a fake executable, but the
installed CLI is not logged in under `--bare`. ArcGIS Online answers
robots.txt with 403, so its layers are "unknown" in the watch and the
South Fulton ZoningCases layer is not checked by `--flags`.

Disagreements and open items (no numeric value differs from
`zoning_rules.json`):

- South Fulton M-1, M-2 `max_height_ft`: the current value is text ("100
  ft or 8 stories, whichever is higher"). The candidate has 100 with a
  whichever qualifier: not comparable by design.
- South Fulton AG-1: 7 values the current file lacks. Lot area and side
  yard have a second value in the same cell, so they are needs_review.
- FID M-1A side and rear: "None. See Section 4.23 ..." read as 0 with a
  cross-reference, so needs_review.
- South Fulton M-1/M-2 side/rear 0: the same as current, but the cell
  carries footnote 19 (12 ft per foot of height over 50 ft next to
  residential), so needs_review.
- Atlanta I-1/I-2 side 5/10: written in words, with adjacency wording,
  so needs_review. The I-2 height text cites 16-17.008 from inside
  16-17.008 on the mirror.
- Fairburn M-1/M-2 height 48: "(unless approved by the fire marshal)".
- Union City width/frontage 150 "for initial zoning request": now
  flagged (finding 5).
- Atlanta rear yard and lot frontage: the code states none.

## DB hygiene

Scratch databases were made with `createdb -T` from a local template on
the `lr-pg` container (`rs23_flags_bb1953` for the first candidate, from
`landtest`; `rs23_fix_*` for this round, from `zoningcheck`), read with
`--local-db-only`, and then dropped. The real checkout's `sources.json`
and `.env` were never touched. Nothing pushed; no `autoresearch/*` ref
touched.

## Not done

- The `--llm` path against a real model (no `ANTHROPIC_API_KEY` here).
- Cross-process rate limiting (GIS back-off while the loop runs instead).
- Case-id-set deltas in the watch. robots.txt bodies are not cached.
- College Park (disabled), Palmetto (the mirror drops the tables),
  Chattahoochee Hills (no industrial district).
