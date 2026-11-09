# 07 — F2 owner clustering (phase 1): reviewer decision (Agent 3)

> Fresh context. I did not write this code. I read the requirement, the risk
> review `01_risk_review.md` § F2, the code writer response
> `06_f2_code_writer_response.md` and the actual diff
> (`git diff 4fe8080..HEAD`; the F2 commits are `783080d`, `8abd3b0`,
> `629f0a8`), then re-ran the checks myself. Model names are omitted.

## Operator answers (2026-11-09, in chat, binding), as honoured here

1. **D3:** COUNT unlisted districts AND districts with unreadable setback
   values (answers D3-01 / D3-02). Root D3 round; nothing in this folder.
2. **D2 flood scope:** "just review". D2 stays as implemented (V/VE
   review-only, floodway still blocks). No flood-rule change. This diff
   touches no flood code.
3. **Null-beats-low:** NOT answered. No new root sub-score. F2 adds no
   score of any kind: the groups are flag text only.
4. **"Fix":** the research.py ArcGIS error-envelope bug, the audit-path bug
   and the D1-11 `zoning_import` fetcher round are root work, not in this
   folder.
5. **"Can you add this yourself":** the agent may apply the reviews/26
   hazards prepare-mutation to `prepare.py`, between runs, under the
   prepare-mutation protocol. Root work; `prepare.py` is not in this diff.
6. **Step 2, screener quick wins:** human-side screener edits are
   authorized except the eval files (`screens.py`,
   `autoresearch/evaluate.py`, `autoresearch/build_eval.py`,
   `autoresearch/data/`, `settings.toml` defaults may only gain new keys).
   No new eval baseline.

## What I verified

| Gate | Result |
|---|---|
| J1 leak | `owner_clusters.build` runs on the sorted, pre-`--limit` list and returns a `Result` held in the `run.main` local `owner_groups`; nothing is written to `p` (tested by deep-copy compare). The only write is `p["flags"].extend(...)` after the sort, the rank numbers and the earthwork attach. Spies on `rank.learned_score`, `scoring.fixed_score`, `screens.site_rejects`, `screens.flags` see no stem key and no `Same owner` flag, slope on and `--no-slope`. On/off runs (build patched to empty, HTTP cache cleared) give identical rank, score, `why_site`, `why_seller`, other flags and request lists. The writer's placement deviation (after the sort rather than beside the slope-skipped flag, because sibling ranks exist only then) is accepted: it is strictly later than every scorer and screen. |
| J2 static | `KEY_STEMS` imported by the test; AST check of `rank.py` / `scoring.py` constants, names and attributes; self-test shows the computed-string limit. `adjacen` is not a stem. |
| J3 matching | Exact `(name_key, address_key)` equality; I read both key functions. Legal forms, numbers and suites kept; ZIP+4 to ZIP5; blank / placeholder names and mailings without a numbered street line give no key. The module names none of `same_owner`, `_same_name`, `_words_agree`, `_near_spelling`, `SequenceMatcher` (AST test). `owners.py` and `fulton.py` unchanged. |
| J4 adjacency | One `LocalProjection` per group, metres, `distance <= gap_ft / FT_PER_M`; STRtree query on the gap-padded box then an exact `distance`; union-find inside one group only. Edge, 5/50 ft and 9.5/10.5 ft both directions, degrees-not-feet, corner, chain, never-across-groups and band cases are tested. `[owners]` is optional with a code default. |
| J5 limit / wording | Counts identical with `--limit 2`; out-of-trial siblings read "not screened in this trial run". I traced `run.main`: in a full run every fetched parcel ends in `survivors` or `rejected` (the `not live` path returns before any report), so that label cannot appear outside a trial run. Wording names band, area and gap; banned words tested. |
| J6 cost / failure | 2,000-parcel single group under the 5 s bar. `GEOSException` is caught at centroid (I confirmed an empty geometry's centroid raises it), projection and pair distance only; other errors propagate; no `stop()`; counted note. |
| J7 output | Cap of 5 plus "and N more", adjacent-first order, statuses, byte-identical `ranked.csv` on reversed input, odd-id escaping through CSV, `map.html` JSON and the brief. `ranked.csv` header equals `report.RANKED_COLUMNS`; `rejected.csv` header unchanged. No new columns. |
| J8 eval | `git diff --stat 4fe8080..HEAD` over `screener/autoresearch`, `screens.py`, `rank.py`, `scoring.py`, `owners.py`, `fulton.py`, `requirements.txt`, `CLAUDE.md`, `prepare.py`, `program.md` is empty, and nothing outside `screener/` and this folder changed. `settings.toml` only gains the `[owners]` table. I re-ran `python -m screener.autoresearch.evaluate`: `pool 2229, positives 34, k 45, hits 2, recall 0.059, positives_screened_out 1, score_range [6.5, 85.0], nonfinite 0`, identical to base. |
| J9 | See Tests. The writer listed the 34 existing `test_decisions.py` tests whose flags gained a line and why each still holds; I accept the reasoning (they assert on decisions, the snapshot and key sets, and the one flags comparison has the line on both sides). README "Owner clusters" covers band/area, exact match, gap and corner touch, the developer-exclusion point and the future eval path. |

## Fix I made

**R-1 (LOW, robustness of the new settings read).** `gap_ft_setting` did
`(cfg.get("owners") or {}).get(...)`. A settings file with a top-level
`owners = 10` (a key, not the `[owners]` table) raised `AttributeError`
with a traceback instead of the clean `ap.error` exit the writer built for
bad values. Change: the table must be a dict, otherwise `ValueError`, which
`run.main` already turns into exit 2 before any fetch. Tests:
`AdjacencyTests.test_settings_key` gains three non-table subcases, and new
`RunTests.test_owners_not_a_table_is_refused_before_any_fetch`. With the
module reverted, all four error with `AttributeError`; with the fix they
pass.

No eval file, `rank.py`, `scoring.py`, `screens.py` or settings default
changed.

## Open items (not blocking)

- **Acreage-mismatch note is partial.** `acreage_mismatch` exists only on
  parcels that reached the map checks, so a sibling screened out at the
  attribute stage (or outside a trial run) never triggers "the parcel map
  disagrees for some". The note is true when shown; its absence is not a
  clean bill. Left as is; a README sentence would do if the operator wants
  it.
- **Accepted under-merge** (README): spellings, other addresses, per-site
  LLCs, `&` spacing, and a ZIP+4 written with a space are not joined. The
  "same address, different name" line is phase 2 with its own review, as is
  the small-neighbour fetch (SR-11).
- Guard limit (SR-16 analog), unchanged: the spy tests live in
  `screener/tests`, which the loop's `evaluate.py` does not run. They hold
  because the loop edits `rank.py` alone and every `exp:` diff gets a human
  read.

## Tests

- Root: `python -m unittest discover -s tests` gives **1744 OK** (base 1744).
- Screener: `python -m unittest discover -s screener/tests -t .` gives
  **254 OK** (134 at `4fe8080`, 208 after F8 and F14, 253 after the F2
  writer, +1 here).
- I did not repeat the live run. The writer's single `--limit 30` run was
  served from the screener's cache (0 network requests), counts only, and
  is this sub-item's one allowed live check; my fix only changes how a
  malformed settings file is refused.

## Decision

**APPROVED after a small fix (R-1).** Gates J1-J9 hold, every F2 risk is
addressed, the eval line is unchanged, no human-owned or eval file changed,
the groups never reach a scorer or screen, and the operator answers are
honoured exactly.

Standing risks:

- SR-6: offline fakes and temp paths; the live run used scratch paths.
- SR-8 analog: no owner name or mailing in any flag or note; counts only.
- SR-10: stdlib + shapely only (AST-checked).
- SR-11: no new request (request lists compared on and off).
- SR-15 / SR-16 analogs: the eval boundary is unchanged; the groups stay in
  a `run.main` local until after scoring and the sort.

## Post-change adversarial review round 1

Operator answers (2026-11-09, recorded as given): D3 counts unlisted
districts and districts with unreadable setback values; D2 flood scope
"just review" (V/VE stays review-only, floodway still blocks; no flood-rule
change); the null-beats-low question is not answered, so no new root
sub-score; "fix" the `research.py` ArcGIS error-envelope and audit-path
bugs and do the D1-11 `zoning_import` round; the agent may apply the
reviews/26 hazards prepare-mutation to `prepare.py`; build the screener
quick wins. The fix below touches no eval file, `rank.py`, `scoring.py`,
`screens.py`, `settings.toml` or `requirements.txt`; `evaluate.fingerprint()`
and `EVAL_FILES` are unchanged.

The round raised one finding.

| # | Finding | Verdict | What changed |
|---|---|---|---|
| 1 (minor) | The README's "Exact match only" bullet says names and addresses match after "punctuation and spacing", but only commas and periods are normalised (plus case, runs of spaces and spaced legal forms): hyphens, apostrophes, `&` and `#` keep their spacing. It also says ZIP+4 is "cut to five digits", while a ZIP+4 written with a space gave no address key at all, so that parcel joined nothing | Real. I confirmed every example offline: `SMITH-JONES` / `SMITH JONES`, `O'NEAL` / `ONEAL`, `A&B` / `A & B`, `#200` / `# 200` give different keys, and `... GA 30303 1234` gave `None`. The code under-merges, so no false assemblage was possible; the defect was a doc promise the code did not keep, and a silently keyless parcel. | Two parts. (a) Code, ZIP only: `_ZIP_END_RE` now takes the +4 after a hyphen, a space or nothing, so a space ZIP+4 is cut to ZIP5 as the README always said. This adds joins only between addresses that already agree to the last character apart from the +4, and the name must still match exactly; a lone trailing four-digit number is still no ZIP. (b) Docs: the README bullet now lists exactly what is normalised and names the four pairs that do NOT join. I did not widen the name normalisation for hyphens, apostrophes or `&`: each would add merges the phase 1 risk review did not weigh (for example `A&B` vs `AB`), and under-merging is the safe side for a flag. That stays a phase 2 question. New tests: `test_space_separated_zip4_is_cut_not_dropped` (space, double space, hyphen and no separator all equal the ZIP5 key; a lone four-digit tail is no key; different ZIP5s stay apart; it fails with the old pattern) and `test_documented_limits_of_normalisation` (pins the README's examples, joined and not joined). Module and `address_key` docstrings updated. |

The earlier "Accepted under-merge" open item is corrected accordingly: a
ZIP+4 written with a space is now joined; `&` spacing, hyphens, apostrophes
and `#` spacing remain accepted under-merge and are now stated in the
README.

Tests after round 1:

- Root: `python -m unittest discover -s tests` gives **1744 OK**.
- Screener: `python -m unittest discover -s screener/tests -t .` gives
  **256 OK** (254 before round 1, +2).

No live run was repeated: the change is a key-normalisation rule tested
offline, and it adds no request.

**Round 1 decision: APPROVED after fixes.**
