# 03 — Terms block and local official files: decision note

> **Tier 2.** Design `00_design.md`; pre-change register and dispositions
> `02_risk_review.md`; operator edits `rules_sources_patch.json`. This
> note records the independent **adversarial** post-change review (fresh
> contexts that did not write the code; every finding was challenged by a
> separate skeptic pass and probed with repro scripts on scratch copies,
> never on the real `rules_sources.json`, and never against Zoneomics or
> Municode), and what changed after it. Model names are omitted, as in
> `reviews/23_rules_scraper/`.

## Change

- **`rules_scrape.py`** (protected, between runs only). A code-level terms
  block for `zoneomics.com` and its subdomains (`TERMS_BLOCKED_DOMAINS`),
  distinct from the Municode denylist: such sources and signals are
  skipped with "terms prohibit automated access", never fetched, never
  replayed from the cache, never parsed. Local official files
  (`kind: local_file`) read from the gitignored `rules_inbox/` with no
  network, through the same section splitters, parsers and verifier,
  cited by document identity, never by path.
- **`rules_sources.json`**: unchanged (human-owned). The edits are in
  `rules_sources_patch.json`.
- `research.py`, `zoning.py`, `site_fit.py`, `zoning_rules.json` and every
  other human-owned file: unchanged (`git diff 9824b59` is empty for
  them). No schema change.

## Review verdict: approve-with-fixes (3 high, 1 should-fix, 4 nits; all 8 fixed)

| # | Severity | Finding | Resolution |
|---|----------|---------|------------|
| 1 | high | A percent-encoded host passed every check. `norm_host`, `url_terms_blocked` and `_https_url` read `zoneomics%2ecom` as it was spelled, but requests decodes it and connects to zoneomics.com. A registry source on `www%2ezoneomics.com` loaded (also as `official`), the fetcher sent robots.txt and the page to www.zoneomics.com, and the cache stored and replayed it. `library.%6Dunicode.com` passed the Municode denylist the same way (that gap was older). It needed a human registry edit to reach. | `norm_host` percent-decodes (up to three rounds) before IDNA. `url_terms_blocked` and `_https_url` fail closed on any `%` in the authority, so such a registry URL or host key refuses the registry (exit 1). `host_denied` also checks the decoded host. `_raw_get` re-checks both lists on the host of `requests.Request(...).prepare().url`, the host that would actually be contacted. Tests `test_percent_encoded_host_predicates`, `..._refused_in_the_registry` (mirror and official, source and signal), `..._never_contacted` (fetch, `_raw_get`, robots, redirect), `test_raw_get_backstop_checks_the_prepared_url`, `test_percent_encoded_host_cache_and_signal`. |
| 2 | high | In the patched registry every local-file failure (file missing, pin mismatch, markers absent, scanned PDF) showed in the diff as "removed (section read, value not extracted)". The kept disabled mirror and the disabled prose entry over the same fields mixed "disabled" into the status sets, so no single-status branch matched. The unit tests used one-source registries. | `diff` takes the category from the enabled, unblocked sources when there are any (`active`), so disabled and blocked entries never mask an enabled source's failure. Tests `PatchedDiffTests`: the real registry with the patch applied in memory and the Fairburn standards entry enabled, one test per failure, each asserting its own category and no "removed"; plus a good file that gives values. |
| 3 | high | `label_value` could not read a two-column table PDF: `pdftotext -layout` prints label, spaces, value, and the parser needed a `:`, dash, `\|` or leader. Every field said "label not found", the run exited 0, and the patch note and design note claimed a PDF copy works. | For pdf and txt sources only, the parser also reads the source's raw lines with 2+ spaces or a tab as a separator. The quote stays the normalised line, which must be a whole line of the cut, and goes through the same verifier. HTML is unchanged. A label present without a separator is reported "label found, no separator or value after it on its line". A cut section whose deterministic parser reads no value at all exits 4. Patch note and design note corrected (a wrapped value needs `llm_prose` or `--assisted`). Tests `test_two_column_table_pdf_reads_with_label_value` (the patch's Fairburn markers, WinAnsi em dashes), `test_tab_separated_txt_and_html_unchanged`, `test_label_without_separator_reported_and_exit_4`. |
| 4 | should-fix | The scanned-PDF text said "route to --assisted", but an assisted record for that section was dropped without a count, so the hint could never work. | `NO_TEXT_LAYER` is now "no text layer (scanned PDF): ask for a text PDF; values cannot be verified". An assisted record aimed at a section that was not cut (scanned, not found, source disabled, blocked, unreachable or refused) is counted in "assisted rejected" and named with its reason on one console line. Records outside a `--jurisdiction` selection are not counted. `run` now adds the load-time rejects instead of overwriting the count. Test `test_assisted_record_for_a_scanned_section_is_counted`. |
| 5 | nit | The provenance sniff missed a mirror name split by a soft hyphen or zero-width character. | `mentions_blocked_host` also tests every variant with Unicode Cf characters removed. Test `test_invisible_characters_do_not_hide_a_mirror_name` (raw, entity and percent-encoded forms, and a file that is refused). |
| 6 | nit | The Fulton County / Union City review text was in `note`, not in the review fields. | Kept in `note` on purpose and now said so in `00_design.md` ("Deviation from decision 3"): the registry has no free-text review field, and `terms_reviewed_by`/`_on` are the human's signature. The patch now fills both with `<OPERATOR: ...>` placeholders, and `Registry.terms_reviewed` treats a placeholder as unsigned, so an unsigned patch changes nothing. Test `test_placeholder_terms_signature_is_not_a_review`. |
| 7 | nit | `gismaps.fultoncountyga.gov` (the `fc_layer34_zoning` signal) was not in the patch. | Added to `set_hosts` with the same proposed text and placeholders. |
| 8 | nit | Placeholder start/end markers in an enabled local entry loaded and only failed at run time. | An enabled local_file entry with a placeholder in a section label, start or end marker is refused at load. Test `test_placeholder_markers_refused_when_enabled`. |

No finding was refuted. Each skeptic reproduced its finding against
0e7ce3e, or confirmed it from the code. Of the 17 new tests, 16 fail on
0e7ce3e (the good-file case of `PatchedDiffTests` is a control); all pass
now.

Also fixed (cheap): `_run_capped` closes the pdftotext pipe, so the suite
no longer prints ResourceWarnings. The PDF test helper uses WinAnsi
encoding so fixtures can carry em dashes.

## What the reviewers verified

- The repro scripts on scratch copies of the registry (the real file's
  sha256 was unchanged), with fake HTTP: no request left the process.
- The patched registry with one local entry enabled, offline, in a
  throwaway clone: the four failure cases and their diff rows.
- A Municode-shaped PDF with the patch's Fairburn markers, read through
  `pdftotext -layout`.
- The standing risks: SR-1 (no human-owned file written), SR-6 (all tests
  offline, throwaway checkouts), SR-7 (no new write path), SR-10
  (`requirements.txt` unchanged, no OCR), SR-11 (blocked hosts get zero
  requests).

## Evidence

- `python -m unittest discover -s tests`: **1359 OK** (was 1342; 17 new
  tests in `tests/test_rules_local_files.py`, which now has 72).
  `python -m unittest discover -s screener/tests -t .`: **134 OK**.
- `tests/zoning_golden_check.py`: identical to the ba4a780 record.
  `tests/site_fit_golden_check.py`: identical to the 28d3205 record (also
  with south_fulton verified).
- Offline run of the committed registry from `rules_cache/` (no network):
  FID and Union City replayed, 22 deterministic values, 7 Zoneomics
  sources skipped "terms prohibit automated access", diff "same" 20,
  "source blocked (terms)" 50, exit 0, as before the fixes.
- `git diff 9824b59 --stat -- research.py zoning.py site_fit.py
  rules_sources.json zoning_rules.json building_prototypes.json
  sources.json program.md prepare.py parameters.json requirements.txt
  screener/` is empty. No `autoresearch/*` ref exists or was touched.
  Nothing was pushed. Zoneomics and Municode were never contacted.

## Operator steps (between runs)

1. Apply `rules_sources_patch.json` to `rules_sources.json` by hand (see
   `00_design.md` "Registry patch") and commit it. FID and Union City keep
   working before and after.
2. Check the terms or disclaimer of `www.fultoncountyga.gov`,
   `www.unioncityga.gov` and `gismaps.fultoncountyga.gov` yourself; then
   replace both `<OPERATOR: ...>` review placeholders with your name and
   the date. Until then, fetching needs `--allow-unreviewed-terms`.
3. Get the official text: South Fulton UDO (Sec. 403 Table 4-1, Table 2-4
   / Sec. 207.06, Sec. 405, Sec. 510) and Fairburn Chapter 80 (Sec. 80-85,
   80-86, 80-337, 80-367, 80-372), from the city clerks or a Municode
   export you download yourself. Ask for a text PDF or HTML, not a scan.
4. Put each file at the patch's path under `rules_inbox/` (gitignored;
   never commit it). For a PDF, set `format: pdf`; the South Fulton tables
   as a PDF go to `llm_prose` or `--assisted`.
5. In each entry you enable: fill `title`, `obtained_from` (the channel)
   and `obtained_on`, re-derive every start/end marker, label and column
   header from the file, and set `enabled: true`. Keep `sha256: "record on
   first use"` or pin it. Commit the registry.
6. Run `make rules-scrape` (add `--assisted FILE` for prose sections).
   Exit 0 means every enabled section was read and gave values; exit 4
   names what to fix (file missing, pin mismatch, markers not found, no
   text layer, labels that read no value).
7. Re-cite `zoning_rules.json` values to the official text by hand, per
   value, from the candidate under `rules_out/`. Moving a jurisdiction
   from draft to verified needs a new run and baseline.
8. Optional: delete the old Zoneomics entries in `rules_cache/`, and
   disable the three `atl_*` GIS signals (Atlanta is out of scope).

## Not done

- The real South Fulton and Fairburn files are not in hand, so their
  markers, labels and headers are not validated against official text.
- No OCR for scanned PDFs (SR-10).
- A label whose value wraps to the next line is not read
  deterministically (it exits 4 and names the label).
