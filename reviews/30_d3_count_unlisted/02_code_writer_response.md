# 02 — D3 extended (unlisted districts and unreadable setbacks count): code writer response

> **Tier 2** (`STANDING_RISKS.md` § "Change tiers"). Code writer (Agent 2),
> working from the requirement and `01_risk_review.md` (written before any
> code by an independent reviewer). Between runs: no open run, no
> `experiment_log.tsv` touched. Base `4fe8080` (+ the risk review commit
> `444c823`). Model names are omitted. Live evidence is counts only.

## The operator's answers (in chat, 2026-11-09, binding)

| # | Operator answer | What this round does with it |
|---|---|---|
| 1 | "Count them": count UNLISTED districts AND listed districts whose setback values are UNREADABLE (D3-01 / D3-02 of `reviews/29_operator_decisions/03_decision_note.md`, decision 5). | **Implemented here.** Both count for the BTS >= 150K SF test with the default setback, never block, and carry their own investigate text. D3-01 and D3-02 are closed. |
| 1 | D2 flood scope: "just review". | Nothing. D2 stays as implemented (V/VE review-only, floodway still blocks). `git diff hazards.py` is empty. |
| 1 | The null-beats-low question is not answered. | Nothing. No new root sub-score. |
| 3 | "fix": the `research.py` ArcGIS error-envelope bug, the audit-path bug, and the D1-11 `zoning_import` fetcher round. | Separate rounds. None of those code paths is touched here. |
| 4 | "can you add this yourself": the agent, acting for the human between runs, applies the `reviews/26_flood_wetlands` hazards prepare-mutation to `prepare.py`. | Separate round (its own `prepare-mutation:` commit, new run + baseline). `prepare.py` is byte-identical here. |
| Step 2 | Build the screener quick wins. | Separate round under `screener/README.md`. `git diff screener/` is empty. |

## What changed

| File | Change |
|---|---|
| `zoning.py` | `District` gains `listed: bool = False` (the district has a mapping entry in its table). `district_for_stored` sets it. Nothing else; `classify` is untouched (zoning golden check identical). |
| `site_fit.py` | `_Rules.default_kind` and `Projection.setback_default_kind` (`"unlisted"` / `"unreadable"` / None). New constants `DEFAULT_UNLISTED`, `DEFAULT_UNREADABLE`, `BTS_REVIEW_TEMPLATES` (read-only mapping). `_verified_kind` gates every kind on `rules.verified` (and on not `from_rules`). `meets_bts_footprint` accepts the two named kinds besides `setback_from_rules` / `setback_default_listed`; `bts_review_text` returns the kind's text. The constraint line is added in the same place as the D3 line. Module docstring updated. |
| `research.py` | Comments and the BTS docstring only, plus ONE new suggested resolution covering all kinds (not part of the dedup key). No new site_fit name. |
| `reporting.py` | `_bts_use_text` docstring only; the logic already routes through `bts_review_text`. |
| `tests/adjacency_golden_case.py`, `tests/adjacency_golden_check.py`, `tests/test_adjacency_scoring.py` | The explicit expected-diff list `D3U_EXPECTED_DIFF` and `expected_after_d3u(record)`, applied to the frozen 595aa08 record (never regenerated). |
| `tests/test_site_fit.py`, `tests/test_operator_decisions.py` | Inverted tests (renamed) and new failure-mode tests, below. |
| `reviews/29_operator_decisions/03_decision_note.md`, `reviews/22_site_fit/03_decision_note.md` | Pointers here; D3-01 / D3-02 closed with the operator's answer; new run + baseline stated. |

### The texts (one stable text per kind, D3U-04)

- Unlisted: `investigate further: district not listed in zoning_rules.json; 25 ft default setback used`
- Unreadable: `investigate further: district setbacks unreadable in zoning_rules.json; 25 ft default setback used`
- Listed, all absent (D3, unchanged byte for byte): `investigate further: no district setbacks in zoning_rules.json; 25 ft default used`

Each is formatted with the default actually used, names no district, no date
and no parcel. The constraints line before it already names the district.

Resolution for all kinds (D3U-09): `check the district and its setbacks in
the code by hand; a human adds the district or its setback values to the
rules file between runs (reviews/29_operator_decisions,
reviews/30_d3_count_unlisted)`. Resolution is not part of the dedup key, so
open Fairburn PD flags are not rewritten.

## Register responses

| ID | Response |
|---|---|
| D3U-01 | Done. The `dist is None` and rules-unavailable returns are byte-identical (`git diff` shows no change there). The eligibility condition names the two kinds (`setback_default_kind in BTS_REVIEW_TEMPLATES`), never "not from_rules". Test `test_d3u_no_district_rule_never_counts`: with all six jurisdictions verified, no provenance, a jurisdiction not in the rules (`college_park`), an unresolved district (governing jurisdiction missing; the schema refuses that state, so `district_for_stored`'s None is mocked and the schema refusal is asserted), and no machine-readable district all stay at no kind, no BTS, no text; rules unavailable too. |
| D3U-02 | Done. `_verified_kind` returns None while draft; both `_empty` and the ok path use it. Draft halves of every golden check are identical. Tests: draft unlisted (`test_d3u_unlisted_district_counts_with_its_review_text` second half), all-draft unreadable scoring (`D3UUnreadableScoringTests.test_draft_is_inert`), FID with FID verified but South Fulton draft gives no kind (`test_d3u_fid_needs_the_governing_table_verified`). |
| D3U-03 | Accepted as asked by the operator. Flag on every such projection; the decision notes state the first verification after this change needs a new run and baseline. Counts below. |
| D3U-04 | Done. Pinned exact texts; once in constraints, once in notes, once as a `data_gap` (never `actionability_block`) on both paths: `test_unlisted_district_counts_with_its_flag`, `D3UUnreadableScoringTests.test_flag_once_data_gap_on_both_paths`. Absence for other kinds: the PD tests assert no new text; the new-kind tests assert no `INVESTIGATE`. |
| D3U-05 | Done. `BTS_REVIEW_TEMPLATE` and `setback_default_listed` unchanged; the new kinds sit beside them in a separate field. reviews/29 PD tests pass unchanged; `test_not_rewritten_while_open` now also shows the open PD flag still dedups while the unlisted parcel's different key is written. |
| D3U-06 | Option (a): `zoning.District.listed`. A listed district whose `dimensions` is absent or not a mapping is UNREADABLE with source "default (district X has no usable dimensions in the rules)"; an unlisted one keeps "not listed in the rules" (no wording change for Z03/Z05). Test `test_d3u_listed_district_without_dimensions_is_unreadable_not_unlisted`. The district entry itself not being a mapping counts as unlisted. Zoning golden check exit 0. |
| D3U-07 | **Residual, not adopted.** Partial unreadable (some readable, some not) is unchanged: the max of the readable values counts from the rules, with the existing "not a number: not applied" note and no flag. The operator was asked only about the all-default cases. Pinned by `test_d3u_partial_unreadable_is_unchanged`. For the reviewer to record for the operator: the setback used may understate the real one; a flag-only follow-up would not move BTS. |
| D3U-08 | Done. The `_ISSUED` identity check is still first in both functions. research.py's allowed site_fit names are unchanged (`StaticResearchTests` green). `test_a_default_setback_projection_built_outside_the_module_never_passes` now covers each new kind (direct construction, copy, `dataclasses.replace`, positional rebuild, draft made to look verified). |
| D3U-09 | Done, one resolution for all kinds; `test_resolution_text_covers_every_kind` and the per-kind flag tests pin it. |
| D3U-10 | Done. Renamed and inverted: `test_d3_unlisted_district_stays_bts_na` -> `test_d3u_unlisted_district_counts_with_its_review_text`; `test_d3_unreadable_setback_value_stays_bts_na` -> `test_d3u_unreadable_setback_value_counts_with_its_review_text`; `D3ScoringTests.test_unlisted_district_stays_na` -> `test_unlisted_district_counts_with_its_flag`; `test_memo_lines` unlisted half; `test_bts_threshold_boundary` gains the kind halves (149,999 SF still fails but is flagged; draft and unnamed kinds never count). No test deleted. With `site_fit.py`, `zoning.py`, `research.py` and `reporting.py` checked out from `4fe8080`, every inverted and new test fails (site_fit: 9 errors; operator_decisions: 5 failures; adjacency DifferentialTests: 3 failures). |
| D3U-11 | See "Live evidence": the lr_d3 DB holds no corridor copy, so fixture counts are reported. |
| D3U-12 | Done. Confirmed against the real output, it matches the reviewer's predicted table exactly: six entries, all under `verified`: `projections[Z03, Z05].constraints` (the unlisted text inserted right after the setback line), `memo[Z03, Z05].summary_used` ("yes, rated N/A (default setback)" -> "yes (default setback; <unlisted text>)"), `memo[Z03, Z05].development_potential` (the Constraints applied line gains the text; its "used for BTS: no (...)" stays). `verified.scoring`, `verified.executed`, `verified.footnote` and all of `draft` identical; `setback_source` and `rules_label` unchanged. `expected_after_d3u` checks the old text is present before each edit and refuses an already-edited record; `test_d3u_expected_diff_is_exactly_the_change` asserts the raw record differs in exactly these six (state, section, parcel, field) keys. `git diff reviews/*/golden_*.json` is empty. |
| D3U-13 | Kept as the fail-closed fallback; `test_memo_lines` reaches it with a used projection whose district does not resolve. |
| D3U-14 | Accepted: an unlisted district has no coverage cap or FAR; the existing "no lot coverage cap in the rules" line stays and is asserted. The investigate text covers it. |
| D3U-15 | Accepted; counts below. |

## Golden checks (after the change)

| Check | Result |
|---|---|
| `tests/zoning_golden_check.py` | exit 0, identical |
| `tests/site_fit_golden_check.py` | exit 0, identical (also with South Fulton verified) |
| `tests/hazards_golden_check.py` | exit 0, identical |
| `tests/building_comps_golden_check.py` | exit 0, identical |
| `tests/adjacency_golden_check.py` | exit 0 gate off and gate on + empty table, through the six-entry expected-diff list; the record file unchanged |

## Live evidence (counts only, D3U-11)

The throwaway DB `postgresql://postgres:testpw@localhost:55432/lr_d3` has
PostGIS 3.4 but no `parcels` table (no local corridor copy), so no corridor
counts exist. No other database was used. Fixture counts instead, rules
patched in memory only (`tests/zoning_helpers.patch_rules`), compared with
the same run where the new kinds are switched off (the base behaviour):

| Case | Parcels | BTS ratings changed | Investigate flags by kind (per-parcel path / cycle) |
|---|---:|---:|---|
| A: site-fit golden fixture, shipped rules, all six jurisdictions verified | 22 | 0 | none / 0 |
| B: as A with every `unlisted_districts` set to `"not_permitted"` | 22 | 0 | unlisted 3 / 3 (none of the three reaches 150K SF) |
| D3 fixture, Fairburn verified, `unlisted_districts` not_permitted | 2 | 1 (N/A -> WEAK) | unlisted 1, listed (PD, unchanged) 1 |
| D3 fixture, as above with PD front setback "see table" | 2 | 2 (N/A -> WEAK) | unlisted 1, unreadable 1 |

BTS MODERATE counts are unchanged in every case (A 7, B 5 on both sides).
The moved parcels reach only WEAK because unlisted parcels pass S9 as
REZONING (S9 = 1) without a precedent. As the risk review predicted, with the
rules as shipped (`unlisted_districts` "unknown" everywhere, no unreadable
value) nothing moves even when every jurisdiction is verified.

## Tests

- Root: `python -m unittest discover -s tests`: `Ran 1753 tests`, `OK`
  (base 1744; +9, none deleted).
- Screener: `python -m unittest discover -s screener/tests -t .`:
  `Ran 134 tests`, `OK`.

## Gates

| Gate | Status |
|---|---|
| G1 scope | Met (D3U-01/05); case 5 recorded as residual (D3U-07). |
| G2 draft inert | Met. |
| G3 flag text | Met. |
| G4 forgery guard | Met. |
| G5 tests | Met (1753 / 134; inverted tests fail on base). |
| G6 golden | Met; expected-diff list matches the prediction exactly. |
| G7 docs | Met: reviews/29 and reviews/22 notes, site_fit / research / reporting docstrings. The first verification after this change needs a new run and baseline. |
| G8 untouched files | `git diff` empty for `prepare.py`, `hazards.py`, `zoning_rules.json`, `screener/`. |
| G9 live evidence | Counts only; no corridor copy in lr_d3 (fixture counts reported). |

## Standing risks

SR-1: no human-owned file touched (`test_zz_human_files_unchanged` green);
`zoning.py`, `site_fit.py`, `research.py`, `reporting.py` changed between
runs. SR-2: no new SQL. SR-6: all new paths run on fakes. SR-8: counts only.
SR-14: no new `action_type`. SR-15: BTS moves once verified with the new
kinds; new run + baseline before the first verification. SR-16: `_ISSUED`
guard kept. Others: no new surface.

## For the reviewer

- `zoning.py` is touched (D3U-06 option (a)), a protected module: the change
  is one defaulted dataclass field and its value at the single constructor.
- D3U-07 residual needs recording for the operator in `03_reviewer_decision.md`.
