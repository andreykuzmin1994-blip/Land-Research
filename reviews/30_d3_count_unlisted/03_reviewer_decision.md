# 03 — D3 extended (unlisted districts and unreadable setbacks count): reviewer decision

> **Tier 2** (`STANDING_RISKS.md` § "Change tiers"). Reviewer and
> implementer (Agent 3), fresh context: did not write the risk review or the
> code. Read the requirement, `01_risk_review.md`, `02_code_writer_response.md`
> and the actual diff `git diff 4fe8080..HEAD`. Between runs: no open run,
> no `experiment_log.tsv` touched. The strongest available model ran this
> role; model names are omitted from repo files by house style. Live evidence
> is counts only.

## The operator's answers (in chat, 2026-11-09, binding)

| # | Operator answer | Honoured here |
|---|---|---|
| 1 | "Count them": UNLISTED districts and listed districts with UNREADABLE setback values count for BTS with the default setback, never block, flagged for investigation (D3-01 / D3-02). | Yes: the two kinds count; texts say "default" and "not listed" / "unreadable"; `data_gap` only. D3-01 / D3-02 closed. |
| 1 | D2 flood scope: "just review" (V/VE review-only, floodway still blocks). | Yes: `git diff 4fe8080..HEAD -- hazards.py` is empty. No flood-rule change. |
| 1 | Null-beats-low: NOT answered. | Yes: no new root sub-score. |
| 3 | "fix": research.py ArcGIS error envelope, audit path, D1-11 zoning_import fetcher. | Separate rounds; none of those paths is touched here. |
| 4 | "can you add this yourself": the agent applies the reviews/26 hazards prepare-mutation to `prepare.py`, between runs. | Separate round; `prepare.py` is byte-identical here. |
| Step 2 | Build the screener quick wins. | Separate round; `git diff screener/` is empty. |

## What was checked

- **Scope (D3U-01).** `_rules_for`'s `dist is None` and rules-unavailable
  returns are unchanged in the diff. Only the `dims is None` path (unlisted,
  or listed without a usable `dimensions` mapping) and the "no readable
  value, at least one present" path set a kind. The eligibility condition
  names the kinds (`setback_default_kind in BTS_REVIEW_TEMPLATES`), never
  "not from_rules". Partial unreadable (case 5) is untouched.
- **Draft inert (D3U-02).** `_verified_kind` returns None unless
  `rules.verified`; it feeds `_empty`, `_project_row` and the constraint
  line. All five golden checks exit 0 on their draft halves.
- **Texts (D3U-04 / -05).** Unlisted: "investigate further: district not
  listed in zoning_rules.json; 25 ft default setback used"; unreadable:
  "investigate further: district setbacks unreadable in zoning_rules.json;
  25 ft default setback used". Fairburn PD's `BTS_REVIEW_TEMPLATE` output
  is byte-identical (open flags keep their dedup key). The resolution text
  changed, which is not part of the dedup key.
- **Forgery guard (D3U-08).** The `_ISSUED` identity check is still first in
  `meets_bts_footprint` and `bts_review_text`; research.py uses no new
  site_fit name (`StaticResearchTests` green).
- **`zoning.py` (protected module, D3U-06 option (a)).** One defaulted
  field `District.listed` and its value at the single constructor
  (`isinstance(d, Mapping)`); `classify` untouched; zoning golden check
  identical. Accepted.
- **Inverted tests fail on base (D3U-10).** Re-checked independently: with
  `site_fit.py`, `zoning.py`, `research.py`, `reporting.py` from `4fe8080`
  and the new tests, test_site_fit gives 9 errors on the new / inverted
  tests, test_operator_decisions 5 failures, test_adjacency_scoring 3
  failures (the expected-diff check plus the two differential checks).
- **Golden checks (D3U-12).** zoning, site_fit, hazards, building_comps:
  exit 0, identical. adjacency: exit 0 gate off and gate on + empty table,
  through the explicit six-entry `D3U_EXPECTED_DIFF` applied to the frozen
  595aa08 record; the record file is unchanged; the list matches the risk
  review's prediction exactly. See the expected-diff list below.
- **Live evidence (D3U-11).** Confirmed: `lr_d3` has no `parcels` table (3
  public tables, PostGIS only), so no corridor counts exist; fixture counts
  in `02` stand. No other database was used.
- **Protected files.** `git diff 4fe8080..HEAD` is empty for `prepare.py`,
  `parameters.json`, `sources.json`, `program.md`, `zoning_rules.json`,
  `building_prototypes.json`, `rules_sources.json`, `hazards.py`,
  `screener/` and every `reviews/*/golden_*.json`.

## Expected golden diff (adjacency only; all under `verified`)

| Parcel | Section / field | Change |
|---|---|---|
| fulton-Z03 (South Fulton R-3, unlisted) | projections / constraints | unlisted investigate text inserted after the setback line |
| fulton-Z05 (Fairburn R-2 OVERLAY, unlisted) | projections / constraints | same |
| fulton-Z03, fulton-Z05 | memo / summary_used | "used for BTS: yes, rated N/A (default setback);" -> "used for BTS: yes (default setback; <unlisted text>);" |
| fulton-Z03, fulton-Z05 | memo / development_potential | the "Constraints applied" line gains the unlisted text |

Scoring, executed statements, footnote and the whole draft half are
identical; `setback_source` and `rules_label` unchanged. Neither parcel
reaches 150K SF (135,200 SF each) nor the S9 gate in scoring.

## What was found and fixed here

1. **README stale (fixed).** README.md said "A setback that is the 25 ft
   default is never a BTS input, even in a verified jurisdiction", untrue
   since reviews/29 and more so now. Replaced with the three counted cases,
   the flag, and the cases that stay N/A.
2. **reviews/29 "Not done" list (fixed).** Still listed D3-01 as open; now
   marked closed with a pointer here.

No code defect found. No `00_design.md` was written for this round: the
design is the risk review's required design plus the code writer's "What
changed" table; recorded rather than back-filled.

## Residuals for the operator

- **D3U-07, partial unreadable values.** Closed by the post-change
  adversarial review round 1 below (flag added, BTS unchanged). The setback
  used is still the max of the readable values and may understate the real
  one; the flag is what tells the operator to check it.
- **`"dimensions": null` on a listed district.** Closed by round 1 below
  (now the D3 "no district setbacks" text).
- **Metric movement.** With the shipped rules (`unlisted_districts`
  "unknown" everywhere, no unreadable value) nothing moves even with every
  jurisdiction verified. It moves once a human sets `unlisted_districts:
  "not_permitted"` and verifies (unlisted parcels pass S9 as REZONING), or
  enters an unreadable setback.

## Required before the first verification

**The first verification after this change needs a new run and baseline**
(SR-15): BTS now moves for unlisted and unreadable districts in a verified
jurisdiction, as well as for Fairburn PD (reviews/29).

## Tests

- Root: `python -m unittest discover -s tests`: `Ran 1753 tests`, `OK`.
- Screener: `python -m unittest discover -s screener/tests -t .`:
  `Ran 134 tests`, `OK`.
- Golden checks: all five exit 0.

## Standing risks

All standing risks hold. SR-1: no human-owned file touched; `zoning.py`,
`site_fit.py`, `research.py`, `reporting.py` changed between runs. SR-2: no
new SQL. SR-6: fakes only. SR-8: counts only. SR-14: no new `action_type`.
SR-15: new run + baseline before the first verification. SR-16: `_ISSUED`
guard kept.

## Decision

**APPROVED after small doc fixes** (README, reviews/29 "Not done" pointer).

## Post-change adversarial review round 1

Operator answers this round implements (2026-11-09, recorded again here):
D3-01/D3-02 "count them": unlisted districts AND districts with unreadable
setback values count with the default and are flagged for investigation.
D2 flood scope: "just review", no flood-rule change. The null-beats-low
question is not answered, so no new root sub-score.

Two minor findings. Both were checked against the code and both are real.

| # | Finding | Verified | Disposition |
|---|---|---|---|
| R1-1 | A listed district with SOME unreadable setback values (e.g. front "see table", side 0, rear 0) counts from the rules with the max of the readable values (possibly 0 ft) and gets no investigate flag. That is the one reading of "unreadable setback values" without one. | Yes. In `_rules_for`, a non-empty `vals` gives `from_rules` True and `kind` None. `bts_review_text` returned None for every `from_rules` projection. Disclosed as D3U-07. | **Fixed, flag only.** `_Rules.partial_unreadable` and `Projection.setback_partial_unreadable` are set only in a verified jurisdiction (never draft). They are True when the setback is the district's own and at least one front/side/rear value is present but unusable. The flag text is `BTS_PARTIAL_REVIEW_TEXT`, "investigate further: some district setbacks unreadable in zoning_rules.json; max of the readable setbacks used". It is stable, with no district name and no number, so it works as the data_gap dedup key. It does not say "default", because the setback is not the default. It appears right after the setback line in the constraints, in the research notes and as one data_gap flag (same resolution text). The memo reads "yes (verified rules; ...)" and the highlight line "yes (...)", never "default setback". The setback value, `meets_bts_footprint` and every score are unchanged. The default is NOT substituted for the readable values: the operator answered for districts where the default is the only number. Swapping it in here would change BTS on a case nobody was asked about. |
| R1-2 | A listed district with no `dimensions` key, or `dimensions: null`, states no setbacks but got the "district setbacks unreadable" text instead of the D3 "no district setbacks" text. A non-mapping district entry is classed "unlisted". | Partly. The dimensions-absent/null case is real. The non-mapping-entry case cannot be reached: `zoning._validate` rejects any district entry that is not an object ("not an object"), so loaded rules never contain one. | **Fixed for the reachable case.** `zoning.District` gains `dimensions_absent` (default False, so existing constructors are unchanged). It is True for a listed mapping entry whose `dimensions` is absent or null. `_rules_for` sends that case down the D3 path: `default_listed` True, the D3 text, source "default (district X has no dimensions in the rules)". A `dimensions` value that is present but not a mapping (string, list) stays UNREADABLE. `listed` keeps its meaning. `adjacency.py` and `adjacency_import.py` read only `dimensions`/`table_jurisdiction`/`district`, so they are unaffected. No code change for the non-mapping entry (schema-refused). |

Tests added or changed (root):

- `test_site_fit.test_review1_partial_unreadable_is_flagged_bts_unchanged`
  replaces `test_d3u_partial_unreadable_is_unchanged`; the inversion is
  deliberate. It covers front 40 / side "see table" and front "see table" /
  side 0 / rear 0. Setback and BTS are as before, the flag appears once, and
  draft stays inert.
- `test_site_fit.test_review1_fully_readable_district_carries_no_partial_flag`
- `test_site_fit.test_review1_forged_partial_projection_has_no_text`
  (`_ISSUED` guard).
- `test_site_fit.test_review1_listed_district_stating_no_dimensions_gets_the_d3_text`
  (absent and null; draft inert).
- `test_site_fit.test_zoning_district_dimensions_absent_field`
- `test_site_fit.test_d3u_listed_district_without_dimensions_is_unreadable_not_unlisted`
  now covers a string and a list `dimensions` (the absent case moved to the
  D3 test above).
- `test_operator_decisions.Review1PartialUnreadableScoringTests`: one
  data_gap flag on both scoring paths. `strategy_fit` is identical to the
  fully readable district with the same 40 ft setback. Also covers the memo
  lines and draft staying inert.

Golden checks: all five exit 0, with no expected-diff change. The shipped
`zoning_rules.json` has no listed district with partially unreadable
setbacks and none without a `dimensions` mapping. The scan found every
listed district with a mapping (Fairburn PD = `{}`), so nothing moves on
today's data. The new-run + baseline requirement above still applies
unchanged.

Suites after round 1: root `Ran 1761 tests`, `OK`; screener `Ran 134
tests`, `OK`.

Round 1 decision: **APPROVED**. Both findings fixed with tests, no human-owned
file touched. `zoning.py` (protected) gains one defaulted field, and the
zoning golden check is unchanged.
