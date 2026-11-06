# Terms block and local official files — design

> Tier 2 (external-service integration and the scraper's source contract).
> The operator set the decisions below; the pre-change risk register
> (LF-01..LF-27) came from an independent reviewer, and its disposition is
> `02_risk_review.md`. The registry edits the operator should make are in
> `rules_sources_patch.json`. Model names are omitted, as in
> `reviews/23_rules_scraper/`.

## Why

The operator reviewed Zoneomics' terms. Its Terms of Use
(https://www.zoneomics.com/terms-of-use, last revised November 2020) bind
every visitor "whether or not you are a registered user" and prohibit
"any robot, spider or other automatic device, process or means to access
the Website for any purpose" and "any manual process to monitor or copy
any of the material on the Website". The Subscription Service Agreement
bans automated or non-automated scraping of Zoneomics Data. Automated
Zoneomics access is therefore off-limits, whatever robots.txt says. The
operator will obtain official ordinance text for South Fulton and Fairburn
(from the city clerks, or a Municode export downloaded by the operator as a
human) and drop the files into the repository. Atlanta is out of scope and
stays draft.

## Pieces

| Piece | Change |
|---|---|
| `rules_scrape.py` | Terms block (`TERMS_BLOCKED_DOMAINS`), local-file sources (`kind: local_file`), provenance sniff, format and parser checks, scanned-PDF detection, capped pdftotext, document cites, new statuses and diff categories. |
| `.gitignore` | `rules_inbox/` (comment on its own line). |
| `Makefile`, `README.md` | `rules-scrape` help text; README section "Terms block and local official files". |
| `tests/test_rules_local_files.py` | New, 55 tests (LF-01..LF-23). `tests/test_hermetic._NEVER_COMMIT` gains two `rules_inbox/` paths. |
| `rules_sources.json` | **Unchanged** (human-owned). The edits are in `rules_sources_patch.json`, validated on a throwaway copy. |
| `research.py`, `zoning.py`, `site_fit.py`, `zoning_rules.json` and every other human-owned file | **Unchanged.** `git diff 9824b59 -- research.py zoning.py site_fit.py` is empty. Both golden checks are identical. |

## Terms block

- `TERMS_BLOCKED_DOMAINS = frozenset({"zoneomics.com"})`, a code constant.
  No registry key, CLI flag, environment variable or parameter changes it.
  `terms_blocked(host)` works on a normalised host: percent-decoded, lower
  case, no userinfo, no port, no trailing dot, IDNA form. It matches the domain and
  every subdomain (`h == d or h.endswith("." + d)`). `url_terms_blocked`
  also fails closed on a userinfo or backslash trick, on any `%` in the
  authority (requests decodes `zoneomics%2ecom` before it connects) and on
  an unparsable URL. A registry URL or host key with `%` in the authority
  refuses the registry. `host_denied` (Municode) checks the decoded host
  too, and `_raw_get` re-checks both lists on the host of the URL requests
  actually prepares (review round 1, finding 1).
- It is distinct from the Municode denylist (`host_denied`), which makes a
  registry fail to load. A registry source on a blocked host loads as a
  `SourceDef` with `blocked_reason`, so FID and Union City keep working
  before the operator edits the registry. A blocked change signal loads
  the same way.
- Where it is checked: in `validate_registry` (flag the source or signal),
  in `scrape()` before the online and offline branches (status
  `blocked_terms`, reason "terms prohibit automated access", no
  `_document_for`, no section cut, no extractor, no assisted record), in
  `PoliteFetcher._check` before `robots_for`, in `robots_for` (no
  robots.txt request; its redirect targets too), on every `fetch()`
  redirect target, in `_raw_get` as a backstop, in `Cache.load` (the URL,
  and the recorded `url`/`final_url`, before the body is read) and in
  `Cache.store`, in `gis_signal`, in the `--watch` signal loop and in
  `compute_flags`.
- A known mirror or blocked host declared `official` still refuses the
  whole registry (exit 1), as in round 1 (F6): that is a relabelled mirror,
  an integrity problem. Filling the terms review fields, or passing
  `--allow-unreviewed-terms`, does not lift the block.
- Outcome and reporting: the console prints one line naming the skipped
  sources. They are not counted for exit 4. The diff category is
  "source blocked (terms)", distinct from "source unreachable" and "source
  disabled". `--watch` rows are `skipped`, and the previous watch baseline
  of a blocked source is kept as it was. An official URL that redirects to
  a blocked host is "unreachable: terms prohibit automated access", which
  does count for exit 4 (a registered official source that no longer
  works).
- The cached Zoneomics pages in `rules_cache/` (7 entries from the first
  live run) can no longer be used. The agent did not delete them. Deleting
  them is the operator's call (`02_risk_review.md`, LF-25).

## Local files

Registry entry (`kind: local_file`; its own key set, no `url`):

```json
{"key": "fairburn_ch80_standards_local", "kind": "local_file", "enabled": false,
 "path": "fairburn/chapter80.html", "format": "html", "source_class": "official",
 "parser": "label_value", "title": "<document title>",
 "obtained_from": "City Clerk certified copy", "obtained_on": "2026-11-20",
 "sha256": "record on first use", "sections": [ ... as for url sources ... ]}
```

- **Load checks.** `path` must be a relative POSIX path of `[A-Za-z0-9._-]`
  parts, with no `.`, `..`, hidden part, leading `/`, backslash, NUL, `:` or
  empty part. `format` must be pdf, html or txt, and `source_class` must be
  official. `title` (at most 160 characters) and `obtained_from` (at most 120)
  may not start with `/`, `\` or `~`, and may not contain `@`, `file:`,
  `rules_inbox`, the file's name, a mirror or blocked host (also spelled
  without a scheme), or a denylisted URL. `obtained_on` must be an ISO date
  between 2000-01-01 and today. `sha256` must be 64 lower-case hex digits
  or `"record on first use"`. An enabled entry with a placeholder (`<`,
  `>` or `TODO`) in title, obtained_from, obtained_on or any section
  label, start or end marker is refused. Parser and format must match (all sources):
  `district_grid` and `use_table` need html, `pdf_grid` needs pdf, and txt
  allows `label_value` and `llm_prose` only.
- **Read (`read_local`).** The inbox must exist, must not be a symlink,
  must resolve inside the checkout (not `.git`), and `git check-ignore` must
  report both the inbox and the file as ignored, which also means the file
  is not tracked. Each folder is opened with `O_NOFOLLOW|O_DIRECTORY`
  relative to its parent's descriptor. The file is `lstat`ed first (it
  must be regular, have one link and fit the size cap; a FIFO, device or
  folder is refused without being opened), then opened with
  `O_NOFOLLOW|O_NONBLOCK` and `fstat`ed. The inode must be the one checked
  (no swap) and must not be `.env` or a human-owned or protected file. The
  bytes are read from that descriptor, capped at `MAX_LOCAL_BYTES` + 1,
  and the bytes read are the bytes hashed. There is no network and no
  cache: local bytes are never copied to `rules_cache/`.
- **Checks before parsing.** Format against the magic bytes (`%PDF-` for
  pdf; UTF-8 without NUL for html/txt; no HTML in a txt source). The pin
  (a mismatch is refused: "file sha256 differs from the pinned value
  (operator replaced file? update the pin)", exit 4, nothing parsed). The
  provenance sniff: the raw bytes, the decoded text, the extracted text
  and the pdfinfo metadata must not name a mirror or blocked host (also
  behind entities, percent-encoding or inserted whitespace). That covers
  the canonical link, og:url, the "saved from url" comment, `<base href>`,
  any href/src and a printed footer URL. Parser caps: 20 000 characters
  per text line and 20 000 cells per table.
- **Same pipeline.** The bytes become a `Fetched` with the synthetic
  identity `local:<key>:<sha256>`. They go through `_document_for` (a new
  `text_document` for txt, which keeps the original lines), `cut_section`,
  the same parsers and `_emit` -> `verify`. There is no new emit path.
- **Cite.** `official: <title>, <section label> (obtained from <channel>,
  <date>)`. `cite_class` reads the `official:` prefix as official, or as
  mixed when the cite also names a mirror or blocked host. The diff
  compares document identities: a mirror-cited current value with an
  equal local value is "same value, new official cite", and two different
  local documents give "same value, new cite".
- **Candidate.** The `_candidate.sources` entry of a local source holds
  kind, title, class, status, reason, sha256, `sha256_pinned`,
  `obtained_from`, `obtained_on`, `provenance_check` and sections. It holds
  no url, path or file name. `watch_state.json` holds the observed sha256
  (the record for "record on first use"). `--watch` reports a different
  sha256 as "changed (operator replaced file)", with the section hashes
  compared as usual.
- **Diff.** A disabled or terms-blocked entry over the same district and
  field (a kept mirror, the second local entry still off) never masks why
  an enabled source failed: the category is taken from the enabled,
  unblocked sources when there are any (review round 1, finding 2).
- **Statuses.** `read` (ok). `refused` (path, file, format, pin or
  provenance problem; exit 4). `unreachable: file not in rules_inbox/`
  (exit 4). Error texts name the source key and a reason class only:
  never a path, file name or file bytes.

## Official formats

| Format | Deterministic | Otherwise |
|---|---|---|
| Municode-style HTML export (div wrappers, `Sec. 80-85. - ...` headings, tables in divs, `<sup>` footnotes) | `label_value`, `district_grid`, `use_table` work. Tested on a synthetic export, including the round-1 chain rule. | — |
| City PDF with a text layer | `label_value` when label and value share a line, separated by `:`, a dash, a dot leader or `\|`, or (pdf and txt only) a run of two or more spaces or a tab, which is how `pdftotext -layout` prints a two-column table. `pdf_grid` for aligned grids. Tested on PDFs built in the test, including a two-column table with the patch's Fairburn markers. | A value that wraps to the next line is reported "label found, no separator or value after it on its line" (the run exits 4 only if no value at all is read from that section; otherwise exit 0, so check the report): use `llm_prose` or `--assisted`. Merged-header tables (South Fulton Table 4-1 as a PDF) go to `llm_prose` or `--assisted`. |
| Scanned PDF (no text layer) | — | "no text layer (scanned PDF): ask for a text PDF; values cannot be verified" (exit 4). No OCR dependency (SR-10). The verifier needs text to check a quote against, so a scan cannot be verified, and an `--assisted` record aimed at such a section is rejected and counted, with its reason on the console. Ask the clerk for a text PDF. |
| Plain text | `label_value` (same separators as a PDF) | Tables lose their structure, so `district_grid`, `use_table` and `pdf_grid` are refused at load. Use `llm_prose` or `--assisted` for tables. |

A section that is cut but whose deterministic parser reads no value at all
exits 4 ("found but no value read"), so a copy whose labels do not fit is
never a silent exit 0.

`pdftotext` now runs with the 120 s timeout and a 64 MB stdout cap (the
process is killed beyond either). `pdfinfo` runs only for local PDFs.

## Registry patch (decision 3)

`rules_sources_patch.json` lists the edits. Apply them by hand to
`rules_sources.json` and commit, between runs:

1. `disable_sources`: set `enabled: false` on all 8 Zoneomics sources
   (South Fulton x2, Fairburn, Atlanta x2, East Point, Hapeville; College
   Park is already off).
2. `set_hosts`: replace four host entries. Zoneomics `terms_url` is now
   `/terms-of-use`, with the operator's finding in `note`. For
   `www.fultoncountyga.gov`, `www.unioncityga.gov` and
   `gismaps.fultoncountyga.gov`, `note` carries the proposed review text
   "reviewed: no terms page found; public government document", marked
   NOT CHECKED BY THE AGENT and `<OPERATOR SIGNS>`, and the two review
   fields hold `<OPERATOR: ...>` placeholders for the human's name and
   date. **Deviation from decision 3, on purpose:** the registry has no
   free-text review field, and `terms_reviewed_by`/`_on` are the human's
   signature (they decide whether the tool may fetch without
   `--allow-unreviewed-terms`), so the agent puts the proposed text in
   `note` and leaves the signature to the human. `Registry.terms_reviewed`
   treats a placeholder as unsigned, so applying the patch unsigned
   changes nothing. A human checks the footer (Terms, Disclaimer, Site
   Policies; Akamai bot policy for Union City; the ArcGIS server's terms
   for gismaps) and replaces both placeholders.
3. `set_jurisdiction_notes`: replace those jurisdictions' `note`.
4. `add_sources`: append these entries to the jurisdiction's `sources`.
   South Fulton gets three sources over one file
   (`rules_inbox/south_fulton/udo.html`): Sec. 403 Table 4-1 as
   `district_grid`, Sec. 207.06 Table 2-4 as `use_table`, and Sec. 405 and
   Sec. 510 as `llm_prose`. Fairburn gets two sources over
   `rules_inbox/fairburn/chapter80.html`: Sec. 80-85 and Sec. 80-86 as
   `label_value` (an html or a text PDF copy; see "Official formats"), and Sec. 80-337, 80-367 and 80-372 as `llm_prose`. All are
   `enabled: false` with `<OPERATOR: ...>` placeholders and `sha256:
   "record on first use"`. Before enabling one: put the file at that path,
   fill title, `obtained_from` (name the channel: "City Clerk" or
   "Municode export downloaded by operator") and `obtained_on`, and
   **re-derive every start/end marker, label and column header from the
   actual file**. The given table markers are the mirror's and may not
   match official text. The prose markers are placeholders. If a file is
   a PDF, change `format` and, for the two tables, the parser (see the
   table above).
5. Optional (`optional_disable_change_signals`, LF-24): disable the three
   `atl_*` GIS signals (about 17 requests to gis.atlantaga.gov per
   `--watch`/`--flags` run for an out-of-scope city).

A test (`RegistryPatchTests`) applies the patch to a copy outside the
repository and loads it. It checks that the real file's bytes are
unchanged, that only FID and Union City stay enabled, that both official
hosts are still unreviewed, that the new `terms_url` is set, and that an
enabled placeholder entry is refused.

## Jurisdiction status after the patch

| Jurisdiction | Source after the patch | zoning_rules.json |
|---|---|---|
| fid | official URL (Fulton County Art. 10 PDF) | unchanged |
| union_city | official URL (Sec. 6-13 D PDF) | unchanged |
| south_fulton | official local file (once the operator drops it and enables the entries) | values stay mirror-cited until a human re-cites them |
| fairburn | official local file (same) | same |
| atlanta | disabled, no source; out of scope, stays draft | mirror-cited values kept |
| east_point, hapeville, college_park | disabled, no source | mirror-cited values kept |
| palmetto, chattahoochee_hills | no source (as before) | unchanged |

`zoning_rules.json` still holds 149 `zoneomics` references in cites. They
keep feeding scoring exactly as before while every jurisdiction is
`draft`. In the diff they show as "source disabled" (after the patch) or
"source blocked (terms)" (before it), never as "removed".

## Live check (2026-11-06)

Run from `/tmp/.../scratchpad/live/live_check.py`. It loads the patched
registry from a scratch copy (`run(..., registry_path=<copy>)`) with one
patched local entry (Fairburn standards) enabled in that copy, with
synthetic provenance and a synthetic Municode-shaped file in
`rules_inbox/fairburn/` (deleted afterwards). Run with
`--allow-unreviewed-terms`, because neither official host has a human
terms review yet. Every request the session sent was recorded.

- **Patched registry**: 4 requests, all to `www.unioncityga.gov` and
  `www.fultoncountyga.gov` (robots.txt + the PDF each). Zero requests to
  Zoneomics or Municode. Exit 0. The synthetic local file was read with
  provenance `passed`, 2/2 sections found, and M-1 front 47 (high), rear
  29 (high), side 13 (medium, footnote) and M-2 front 61 were cited
  `official: Synthetic live-check document (not an ordinance), Sec. 80-85(f)-(g) ...`.
  No output file contained `rules_inbox`, `/home/user`, the file name or
  `/root`. FID and Union City: 4 sections found, and the "same" 20 values
  are unchanged from round 1.
- **Unpatched (committed) registry**: the same 4 requests. The 7 enabled
  Zoneomics sources were skipped "terms prohibit automated access", named
  on one console line. Exit 0. The diff shows "source blocked (terms)" 50.
- **Offline, committed registry**: the 7 Zoneomics cache entries were
  refused ("terms prohibit automated access"), and FID and Union City were
  replayed.
- `sha256(rules_sources.json)` was identical before and after.

## Evidence

- `python -m unittest discover -s tests`: **1342 OK** (was 1287;
  `test_rules_local_files.py` is new with 55 tests, plus 2 paths in
  `test_hermetic`). `python -m unittest discover -s screener/tests -t .`:
  **134 OK**.
- `tests/zoning_golden_check.py`: identical to the ba4a780 record.
  `tests/site_fit_golden_check.py`: identical to the 28d3205 record (also
  with south_fulton verified). The outputs are byte-identical to the runs
  before the change.
- `git diff 9824b59 --stat -- rules_sources.json zoning_rules.json
  building_prototypes.json sources.json program.md prepare.py
  parameters.json requirements.txt screener/ research.py zoning.py
  site_fit.py` is empty. `runner.verify_rules_sources_committed()` passes.
  No `autoresearch/*` ref exists or was touched. Nothing was pushed.

## Not done

- The independent post-change review is recorded in `03_decision_note.md`.
- The real South Fulton and Fairburn files are not in hand, so their
  markers, labels and headers are not validated against official text.
- No OCR for scanned PDFs (SR-10).
- The reviews/23 artifacts and the `rules_cache/` Zoneomics entries are
  left as they are. Both are the operator's decisions (LF-25).
