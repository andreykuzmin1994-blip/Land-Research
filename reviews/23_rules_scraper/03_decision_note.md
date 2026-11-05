# 03 — Zoning-code scraper: implementation note and first candidate

> **Tier 2.** Design `00_design.md`; pre-change register and dispositions
> `02_risk_review.md`. This note is the implementer's record. The
> adversarial post-change review by a fresh context that did not write the
> code is **still to run**; until it has, this change is not approved
> (STANDING_RISKS § Change tiers). Model names are omitted.

## Scope and branch

- Commits on `claude/tender-mayer-1rd4y4`, stacked on the zoning (21) and
  site-fit (22) work: **PR #34's scope has grown** to include this
  scraper. Reviewers of #34 now review this too, or it is split out.
- Nothing pushed. No `autoresearch/*` ref existed before or after.
- `git diff 0418d2c -- research.py site_fit.py zoning.py` is empty.
  `tests/zoning_golden_check.py` and `tests/site_fit_golden_check.py`:
  identical to their records. No schema change. `prepare.py`,
  `parameters.json`, `sources.json`, `program.md`, `zoning_rules.json`,
  `building_prototypes.json`, `requirements.txt`, `screener/` untouched.

## Decisions

1. **Runner checks `rules_sources.json` (RS-08).** Not a scoring input,
   but the operator asked for the same protection as the other
   human-owned files. A dirty file fails `verify_setup` (infrastructure
   path, no experiment, no crash row) and `evaluate` refuses before any
   sub-cycle. The check is a git blob comparison inside `runner.py`.
2. **Loop detection by flock, not by file.** The runner's
   `.experiment_loop.lock` stays on disk after a loop ends; the tool
   probes the lock (shared, non-blocking, without creating the file).
3. **Union City is read deterministically.** The official PDF, blocked
   by Akamai during the survey, answered at the first live run; Sec. 6-13
   D is a numbered `label: value` list, so the registry entry was changed
   from prose to `label_value` (a registry edit, committed separately).
4. **Assisted path for Atlanta prose.** The CLI extractor is wired and
   tested with a fake executable, but the installed CLI is "Not logged in"
   under `--bare` (it accepts only `ANTHROPIC_API_KEY`). The 12 Atlanta
   records were written by hand (`assisted_records.json`), committed after
   the verifier, and passed the same `verify()`.
5. **Terms.** No human has reviewed any host's terms
   (`terms_reviewed_by` is null everywhere). The first live run used
   `--allow-unreviewed-terms`. The law text itself is public
   (government-edicts doctrine), but the mirror's compilation and
   presentation may not be; the committed artifacts hold only values,
   cites and short excerpts (<= 300 characters, tested). Before regular
   use, a human should read the Zoneomics terms (rendered client-side, so
   the survey could not) and fill the review fields.
6. **ArcGIS Online robots.** `services*.arcgis.com/robots.txt` answers
   403, which the robots policy treats as "host refused". The change
   watch therefore reports the South Fulton, Fairburn, Union City and East
   Point layers as unknown, and `--flags` cannot read the South Fulton
   ZoningCases layer. `zoning_import.py` (a separate, older tool without a
   robots check) still reads those hosts; whether that is acceptable is an
   operator question, not changed here.

## First live candidate (2026-11-05)

Run: `python rules_scrape.py --allow-unreviewed-terms --assisted
reviews/23_rules_scraper/assisted_records.json --flags --local-db-only
--no-open-run-check` from a throwaway clone against a local database (see
DB hygiene). Artifacts: `01_first_candidate.json` (the candidate),
`01_first_candidate_diff.md` / `.json` (the diff report).

**Sources**: 10 registered, 9 enabled and fetched, 0 unreachable, 1
disabled (College Park, markers unverified). Requests: www.zoneomics.com
8 (robots.txt + 7 pages), www.fultoncountyga.gov 2, www.unioncityga.gov 2;
Municode never contacted. Sections: 15 of 15 enabled sections found.

**Values**: 98 extraction records — deterministic 86, assisted 12, llm 0.
Verifier: **66 accepted, 32 needs_review, 0 rejected**; 2 section-level
needs_review (East Point lot area "Per conditions specified"); 0 sections
still needing extraction. In the candidate: 75 placed values (53 mirror,
22 official), 8 use statuses copied from `zoning_rules.json` (marked
`copied_from`), 20 unplaced values (East Point and Hapeville: no use
status in the current rules). Placed values with needs_review: South
Fulton 12, Fulton Industrial District 9, Atlanta 4, Fairburn 2.

**Agreement with `zoning_rules.json`, five corridor jurisdictions**
(per jurisdiction x district x field):

| Jurisdiction | same, official source (independent) | same, mirror vs mirror (NOT independent) | changed | added | not comparable | not extracted |
|---|---|---|---|---|---|---|
| South Fulton | 0 | 18 | 0 | 7 | 2 | 0 |
| Fairburn | 0 | 14 | 0 | 0 | 0 | 0 |
| Atlanta | 0 | 12 | 0 | 0 | 0 | 4 |
| Union City | 8 | 0 | 0 | 0 | 0 | 0 |
| Fulton Industrial District | 12 | 0 | 0 | 2 | 0 | 0 |
| **Total** | **20** | **44** | **0** | **9** | **2** | **4** |

By (source row, field), so the merged South Fulton M-1/M-2 row counts
once: same 20, same mirror-vs-mirror 35, added 29 (all jurisdictions),
not comparable 1. "Not covered by a registered source" (27 rows) are
current fields no registered section targets (for example parking,
buffers, Atlanta use status).

**Every disagreement or open item** (no numeric value differs):

- South Fulton M-1, M-2 `max_height_ft`: current is text ("100 ft or 8
  stories, whichever is higher"), candidate 100 with a "whichever"
  qualifier. Not comparable by design; keep the text.
- South Fulton AG-1: 7 values the current file lacks (lot area 43,560 sq
  ft from "1 acre ..." with a second value in the same cell -> needs
  review; side 25 ft with a second value "40' to street" -> needs review;
  rear 50, width 100, frontage 35, height 40, coverage N/A).
- Fulton Industrial District M-1A side and rear: "None. See Section
  4.23 ..." read as 0 with a cross-reference -> needs review (the current
  file has no M-1A side/rear).
- South Fulton M-1/M-2 side/rear 0: same as current, but the cell is
  "None. [fn 19] See Sec. 405 ..." -> needs review (footnote 19 adds 12 ft
  per foot of height over 50 ft next to residential).
- Atlanta I-1/I-2 side 5/10: "at least five/ten feet" in words, with
  adjacency wording -> needs review. Atlanta I-2 height cites "section
  16-17.008" from inside 16-17.008 on the mirror (likely 16-17.006 in the
  official code) -> needs review.
- Fairburn M-1/M-2 height 48: "(unless approved by the fire marshal)".
- Union City lot width/frontage 150 ft "for initial zoning request"
  (no qualifier pattern caught "for initial"; read it by hand).
- Atlanta rear yard and lot frontage: the code states none (current
  null too) -> "not extracted".

**Watch** (two consecutive live runs in the real checkout, before the
page-wide Ord. No. change): first run 54 unknown (no previous state); second
run 30 unchanged, 24 unknown, 0 changed. Unknown: the ArcGIS Online
layers (robots 403), every layer's lastEditDate (no `editingInfo` on the
Atlanta and Fulton County MapServer layers), and pages without an
"Ord. No. ... date" pattern. South Fulton chapter 4's newest dated
ordinance in the cut section was 2021-12-21 (flagged STALE at > 365 days;
the page-wide date is now used).

**Flags** (local database, 1,070 parcels with imported zoning): 113
parcels with conditions on file (177 hits: 23 Atlanta `-C` districts and
154 Atlanta rezoning cases with status Complete), 42 parcels with other
cases (Correction 32, Filed 15, Pending 3, Denied 2 hits); South Fulton
ZoningCases not checked (robots 403). 70 (jurisdiction, district) pairs
are in the GIS but not in `zoning_rules.json` (residential, commercial,
mixed-use and SPI districts — expected, `unlisted_districts` covers them).
Parcel ids stayed in the gitignored `rules_out/conditions_flags.json` of
the clone.

## DB hygiene (RS-30)

Database `rs23_flags_bb1953` created with `createdb -T landtest` on the
local PostGIS container (`landtest` never written), filled by
`zoning_import.py` run from a throwaway clone (own `.env`, patched
`sources.json`), read by `rules_scrape.py --flags --local-db-only`, then
dropped. The real checkout's `sources.json` and `.env` were never touched.

## Not done

- The post-change adversarial review (fresh context), including an
  independent re-verification of the 12 assisted records against the
  cached sources.
- The `--llm` path against a real model (no `ANTHROPIC_API_KEY` here).
- Cross-process rate limiting (GIS back-off while the loop runs instead).
- Case-id-set deltas in the watch; robots.txt bodies are not cached.
- College Park (markers unverified, disabled), Palmetto (mirror drops the
  tables), Chattahoochee Hills (no industrial district).
