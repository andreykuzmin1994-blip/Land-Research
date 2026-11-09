# 06 — Screener quick wins: code writer response (F2 owner clustering, phase 1)

> Response to `01_risk_review.md` § F2, written after the code. Each risk
> item and gate has its disposition and the test that pins it. Tests are in
> `screener/tests/test_owner_clusters.py` unless named otherwise. Base for
> this item `783080d` (F2 risk review), on `4fe8080`. No eval file,
> `rank.py`, `scoring.py`, `screens.py`, `snapshot.py`, `owners.py`,
> `fulton.py`, `build_eval.py`, `autoresearch/data/`, `PROGRAM.md` or
> `requirements.txt` was touched. `settings.toml` only gains a new
> `[owners]` table with one key.

## Operator answers this round (2026-11-09, in chat, binding)

Restated so this response stands alone:

1. **D3 (D3-01 / D3-02):** COUNT unlisted districts AND districts with
   unreadable setback values too. Root D3 round, not this folder.
2. **D2 flood scope:** "just review". D2 stays as implemented (V/VE
   review-only, floodway still blocks); no flood-rule change.
3. **Null-beats-low:** not answered. No new root sub-score lands. F2 adds
   no score of any kind: the groups are flag text only.
4. **"Fix":** the research.py ArcGIS error-envelope bug, the audit-path bug
   and the D1-11 `zoning_import` fetcher round. Root work, not this folder.
5. **"Can you add this yourself":** the agent, acting for the human between
   runs, applies the reviews/26 hazards prepare-mutation to `prepare.py`.
   Root work under the prepare-mutation protocol, not this folder.
6. **Step 2: build the screener quick wins.** Human-side `screener/` edits
   are authorized except the eval files (`screens.py`,
   `autoresearch/evaluate.py`, `autoresearch/build_eval.py`,
   `autoresearch/data/`, `settings.toml` defaults). No new eval baseline.

## What changed

| File | Change |
|---|---|
| `screener/owner_clusters.py` (new) | `name_key`, `address_key`, `build`, `describe`, `summary_notes`, `gap_ft_setting`. Stdlib + shapely, plus `.geo` (`LocalProjection`, `FT_PER_M`) and `.owners.normalize` (read only). |
| `screener/run.py` | Reads `[owners] adjacency_gap_ft` once (bad value → `ap.error`, exit 2, before any fetch). Calls `owner_clusters.build` on the sorted, pre-`--limit` parcel list into the local `owner_groups`, and adds its counts-only summary notes. After the sort and the earthwork attach, builds each sibling's status (ranked `#rank`, screened out with `reason_key` of its first reason, or not screened in this trial run) and extends `p["flags"]` of each ranked parcel with `describe(...)`. |
| `screener/settings.toml` | New `[owners]` table, `adjacency_gap_ft = 10`, with a comment. No existing key or default changed. |
| `screener/README.md` | A "Tune it" bullet and an "Owner clusters" section: the band/area limit, exact match only, the gap and corner-touch meaning, the X5 point (cannot widen the developer exclusion), not a score, and the future eval path. |
| `screener/tests/test_owner_clusters.py` (new) | 45 tests. |

**Deviation from the requirement's placement.** The requirement put the
lines "next to the slope-skipped flag". They need each sibling's rank,
which exists only after the sort, so they are appended after the sort and
the earthwork attach, at the end of `p["flags"]`. This is still after
`scoring.score` and `screens.flags` (X7), and the flag list and its
reports (`ranked.csv`, `ranked.geojson`, `map.html`, brief "Watch-outs")
are the same.

## Risk dispositions

| ID | Sev | Disposition | Tests |
|---|---|---|---|
| F2-1 | HIGH | **Held, structural.** No key is ever put on a parcel. `build` returns a `Result` held in the `run.main` local `owner_groups`, keyed by `parcel_id`. Duplicate ids are left out of every group and counted in a note (not an assert, so a future adapter's duplicates degrade to a note rather than a crash). The only write is `p["flags"].extend(...)` after the sort. Spies on `rank.learned_score`, `scoring.fixed_score`, `screens.site_rejects` and `screens.flags` record each call's key set and its flags: no key holds a stem and no flag seen there holds the `Same owner` prefix. Clustering on vs off (`build` patched to return an empty `Result`, fresh parcels, cache cleared): rank, score, `why_site`, `why_seller` and the other flags are identical, slope on and `--no-slope`. | `RunTests.test_spy_slope_on`, `test_spy_no_slope`, `test_scores_order_and_requests_do_not_depend_on_the_groups`, `MatchingTests.test_build_writes_nothing_to_the_parcels`, `test_duplicate_ids_are_left_out_and_counted` |
| F2-1 b | HIGH | **Done.** Stems `KEY_STEMS = ("cluster", "portfolio", "assembl")` live in the module and the test imports them. Not `adjacen`: the test asserts `rank.py` already says "adjacent". The docstring states the computed-string limit, and a self-test shows `"clus" + "ter"` passes the guard. | `StaticGuardTests` (3) |
| F2-2 | HIGH | **Fixed.** Exact equality of `(name_key, address_key)`; groups are equivalence classes, so nothing chains. `name_key` is `owners.normalize` plus spaced legal forms joined (`L L L P`, `L L P`, `L L C`, `L P`); legal forms, roles and numbers are kept; blank or placeholder names give `None`. `address_key` normalises the whole mailing the same way and cuts ZIP+4 to ZIP5; suites kept. It needs fulton's `", "` join, a street line with a digit in it and a ZIP at the end, so "ATLANTA, GA 30310" or a street with no number gives `None`. Keyless parcels join nothing and are counted. The module never names `same_owner`, `_same_name`, `_words_agree`, `_near_spelling` or `SequenceMatcher` (AST check). `owners.py` and `fulton.py` are unchanged. Accepted under-merge (README): spellings, other addresses, per-site LLCs. The weaker "same address, different name" line is out of phase 1. | `KeyTests` (4), `MatchingTests` (10, incl. three `same_owner`-true pairs that do not group) |
| F2-3 | MED | **Fixed.** Clustering runs on `parcels` right after the sort, before the `--limit` slice. A trial run gives the same counts and adjacency line as the full run, and siblings outside the trial read "not screened in this trial run". Wording: "the same tax-roll owner name and mailing address is on N other 5-50 ac parcels in the jurisdictions searched (M ac with this one) … Counts cover only that band and area". The band comes from `[area]`. No line says "owns", "portfolio of the owner" or "contiguous" (test). No second fetch; `fetch_parcels` is unchanged. | `RunTests.test_limit_gives_the_same_counts_and_labels_the_rest`, `DescribeTests.test_wording`, `AdjacencyTests.test_band` |
| F2-4 | MED | **Fixed.** One `LocalProjection` per group at the mean of the members' centroids, then `distance <= gap_ft / FT_PER_M` in metres. `[owners] adjacency_gap_ft`, read with a code default of 10; an old settings file without the table runs. Non-number, bool, negative, NaN and inf values are refused. Components are union-find inside one group only. Assemblage = component of 2+ whose summed tax `acres` lies in `[min_acres, max_acres]`; above → "larger than the … search band". Any member with `acreage_mismatch` adds "acres from the tax record, the parcel map disagrees for some". The line ends "Check the shared boundary: a corner touch is not frontage". | `AdjacencyTests` (8): edge, 5 ft / 50 ft and 9.5 / 10.5 ft both east-west and north-south, degrees-are-not-feet, corner, chain A-B-C, never across groups, band 2×20 / 3×20 / [area] 10-30, settings key. `RunTests.test_old_settings_file_without_owners_table`, `test_gap_and_band_come_from_settings`, `test_bad_gap_setting_is_refused_before_any_fetch` |
| F2-5 | MED | **Fixed.** `shapely.STRtree` over every group's projected shapes, queried with the gap-padded bounds and confirmed with `distance`. A 2,000-parcel single group (50 × 40 touching grid) builds in 0.12 s here; the test bar is 5 s. Only `GEOSException` is caught, in three places: centroid, projection (that parcel is "not checked" and left out of adjacency), and a pair's distance (both ends "not checked"). Not-checked parcels still count in the group, get their own flag line and a counted summary note. Every other error propagates (test). No `stop()`: a GEOS problem is a flag, not a run failure. | `CostAndFailureTests` (5), `RunTests.test_measuring_error_is_flagged_and_the_run_goes_on` |
| F2-6 | MED | **Fixed.** At most 5 sibling ids per line, then "and N more"; order is adjacent first, then by `parcel_id`; counts and acres in full. Statuses: `ranked #n`, `screened out: <reason_key>` (a `pass` / `listed` mark reads through its reject reason), `not screened in this trial run`. No owner name or mailing in any line or note. No new `RANKED_COLUMNS`; `ranked.csv` header equals `report.RANKED_COLUMNS` and `rejected.csv`'s header is unchanged. An id holding `<`, `"` and `;` round-trips through the CSV cell, `map.html`'s data block (no raw `<b>`; JSON parses back to the id) and the brief. | `DescribeTests.test_cap_order_and_statuses`, `RunTests.test_pass_decision_shows_as_screened_out`, `test_runs_are_deterministic`, `test_outputs_escape_odd_ids` |
| F2-7 | MED | **Done.** `county_parcel` defaults are unchanged; the F2 run fixture sets distinct owners and mailings. Existing tests whose output changed are listed below. | — |
| F2-8 | LOW | **Held.** Imports are AST-checked: stdlib + `shapely`, `shapely.errors`, `.geo`, `.owners`. `build_eval.py` / `evaluate.py` do not import the module. `requirements.txt` unchanged. Evaluate line and fingerprint below. | `MatchingTests.test_imports_are_stdlib_shapely_and_two_screener_modules` |
| F2-9 | LOW | **Held.** No request. With the HTTP cache cleared before each run, the sorted request list with groups on equals the list with groups off, slope on and `--no-slope`. No new runtime file. | `RunTests.test_scores_order_and_requests_do_not_depend_on_the_groups` |
| F2-10 | LOW | **Held.** No group id is exposed. Groups are walked in sorted key order and members in id order. Reversing the fetched list gives a byte-identical `ranked.csv`. | `RunTests.test_runs_are_deterministic`, `DescribeTests.test_cap_order_and_statuses` |
| F2-11 | LOW | **Done.** README states the future eval path (exact-key groups on the 2016 snapshot in `build_eval.py`, a FEATURES entry, `--new-baseline`). `PROGRAM.md` untouched. | — |
| F2-12 | LOW | **Followed.** See live evidence below. | — |

## Gates

| Gate | Status |
|---|---|
| J1 | Met: spy tests slope on / `--no-slope`; expected parcels carry the lines; on/off identical. The lines are appended after the sort rather than in the scoring loop (see the deviation note), still after every scorer and screen. |
| J2 | Met: `StaticGuardTests`. |
| J3 | Met: `KeyTests`, `MatchingTests`; `git diff` shows `owners.py`, `fulton.py` unchanged. |
| J4 | Met: `AdjacencyTests` and the three settings run tests. |
| J5 | Met: limit test; wording test. |
| J6 | Met: timing test, GEOS paths, no broad except, no `stop()`. |
| J7 | Met: cap, statuses, determinism, escaping; headers unchanged; no new columns. |
| J8 | Met: see below. |
| J9 | Met: counts below. |

## Existing tests whose output changed (F2-7)

Found by wrapping `owner_clusters.describe` over the whole screener suite
and recording every test in which it returned lines. 34 tests, all in
`screener/tests/test_decisions.py`: `CliTests` (12), `DecisionsFileTests`
(13), `FeatureStoreTests` (1), `LeakTests` (2), `SnapshotTests` (6).

Why: that file's fixture has three parcels with the shared default owner
`TEST OWNER ALPHA` and the shared default mailing (`14F0001 0001`,
`14F0003 0003` and the spaced id), about 900 m apart. They form one group
of three with no adjacency, so each of the three, when ranked, gains one
`Same owner:` line. Why each still holds: those tests assert on decisions,
the snapshot, the store and key sets, none of which the line touches. The
one flags comparison (`LeakTests.test_maybe_changes_no_score_flag_or_order`)
compares two runs that both carry the same line, since a `maybe` mark
changes no sibling's status. No test in `test_integrity.py` or
`test_earthwork.py` changed: `RunHarness`'s two default parcels have
different owners (ALPHA / BETA), and the extra parcels those files add use
other owners.

## Evaluation boundary (J8)

- `git diff --stat 783080d -- screener/autoresearch screener/screens.py screener/rank.py screener/scoring.py screener/snapshot.py screener/owners.py screener/fulton.py screener/requirements.txt`: empty (`screener/autoresearch` includes `PROGRAM.md`, `build_eval.py`, `evaluate.py` and `data/`). Against `4fe8080` the same paths show only F14's new `snapshot.py`.
- `python -m screener.autoresearch.evaluate`, before and after (and after the live run): `pool 2229, positives 34, k 45, hits 2, recall 0.059, positives_screened_out 1, score_range [6.5, 85.0], nonfinite 0`. `fingerprint()` is `4efd44280951`, unchanged. `rank_import_problems()` is `[]`.

## Test counts (J9)

- Root: `python -m unittest discover -s tests`: 1744 OK.
- Screener: `python -m unittest discover -s screener/tests -t .`: 253 OK (134 at base, 208 after F8 and F14, plus 45 here). The new file was also run three more times on its own to check the threaded slope run: OK each time.

## Live evidence (counts only)

One `python -m screener.run --limit 30` through the screener's own
fetchers and cache, driven with `run.DECISIONS_FILE` pointed at a scratch
file and `--out` in a scratch directory outside the repo. It was served
entirely from the screener's cache (fetched earlier today): 0 network
requests counted at `requests.Session.request`. The real
`inputs/decisions.csv` SHA-256 matched before and after, and no
`screener/output/` was created. The scratch files were deleted afterwards.

- Exit 0. Parcels fetched (the full band, pre-limit): 5366. Keyless: 33. Duplicate-id rows: 0.
- Groups of 2 or more: 521, holding 1853 parcels; the largest group has 152 parcels.
- Adjacent sets: 395; 302 inside the 5-50 ac band, 93 larger.
- Adjacency not checked: 0. Clustering wall time: 0.10 s.
- Ranked parcels: 7; with a group line 1; with an adjacency line 1; with "possible assemblage" 0 (that parcel's adjacent set is larger than the band).
- The evaluate line was identical after the run.

Standing risks:
- SR-6: every test runs on fakes and temp paths; the live run used scratch paths.
- SR-8 analog: no owner name or mailing in any flag or note; evidence above is counts only.
- SR-10: stdlib + shapely only (AST-checked).
- SR-11: no new request (request lists compared with groups on and off).
- SR-15 / SR-16 analogs: the eval boundary is unchanged. The groups stay in a `run.main` local until after scoring and the sort; the spy tests are the guard and the human read of `exp:` diffs is the backstop.
