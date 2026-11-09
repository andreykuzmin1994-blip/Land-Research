# 01 — D3 extended (unlisted districts and unreadable setbacks count): pre-change risk review

> **Tier 2** (`STANDING_RISKS.md` § "Change tiers"): the change moves a BTS
> input of the metric once any zoning jurisdiction is verified, and touches
> `site_fit.py`, `research.py` and `reporting.py` (all immutable during a
> run). This register was written by an independent reviewer BEFORE any
> code, from the requirement and the code at base `4fe8080`. It holds no
> code. Severity: blocking / high / should-fix / nit. Model names are
> omitted.

## The operator's answers (in chat, 2026-11-09, binding)

Recorded here as given to this workflow; every later document in this
folder repeats the parts it acts on.

| # | Operator answer | What it means for this round |
|---|---|---|
| 1 | "Count them": count UNLISTED districts AND listed districts whose setback values are UNREADABLE (answers D3-01 and D3-02 of `reviews/29_operator_decisions/03_decision_note.md`, decision 5). | **This round.** Both cases count for the BTS >= 150K SF test with the default setback, never block, and carry an investigate flag. |
| 1 | D2 flood scope: "just review". | No flood-rule change. D2 stays as implemented (V/VE review-only, floodway still blocks). Out of scope here; `hazards.py` must show an empty diff in this round. |
| 1 | The null-beats-low question is NOT answered. | No new root sub-score in this round or any round until answered. |
| 3 | "fix": the `research.py` ArcGIS error-envelope bug and the audit-path bug, and the D1-11 `zoning_import` fetcher round. | **Separate rounds**, each with its own review. Not part of this change; this change must not touch those code paths. |
| 4 | "can you add this yourself": the agent, acting for the human between runs, applies the `reviews/26_flood_wetlands` hazards prepare-mutation to `prepare.py`. | **Separate round.** Not part of this change. The reviewer notes for that round: `CLAUDE.md` says "NEVER edit `prepare.py`" and routes the mutation to the human; that round must record the operator's delegation verbatim, run between runs only (no open run), use a `prepare-mutation:` commit and plan the new run + baseline (SR-15). This change must leave `prepare.py` byte-identical. |
| Step 2 | Build the screener quick wins. | **Separate round** under `screener/README.md`. Not part of this change. |

## What the code does today (verified at 4fe8080)

- `site_fit._rules_for` (site_fit.py ~704-772) has four "default setback"
  paths:
  1. `dist is None`: no stored zoning / no provenance / no district /
     jurisdiction or governing jurisdiction absent from the rules /
     `normalize` gives None, or the rules are unavailable. `verified` is
     always False here.
  2. `dims is None`: the district is not in the jurisdiction's table
     ("unlisted"), **or** it is in the table but has no `dimensions`
     mapping (`zoning.district_for_stored` line 518 returns `dims=None` for
     both; the setback source then says "not listed in the rules" even for
     the second case).
  3. dims present, every front/side/rear absent or null: the D3 case
     (`default_listed=True`, today only Fairburn PD).
  4. dims present, no readable value but at least one present-but-unusable
     value (non-number, string, bool, NaN, negative): "unreadable".
  A fifth case is NOT a default: some keys readable and some unreadable;
  the district max of the readable ones is used, `from_rules=True`, it
  counts today, with a "not a number: not applied" note and no flag.
- `meets_bts_footprint` (927-942) passes only `_ISSUED` projections with
  `rules_verified and (setback_from_rules or setback_default_listed)`.
  `bts_review_text` (944-955) returns `BTS_REVIEW_TEMPLATE` only for case 3.
- research.py computes a projection ONLY for a parcel that passes the S9
  gate (`_zoning_verified`: verified jurisdiction AND class by_right /
  conditional / rezoning), on both the per-parcel path (`_fetch_projection`)
  and the cycle path (`verified_ids`, ~2528). research.py 3196-3202 appends
  `bts_review_text` to the notes and writes it as one `data_gap` flag via
  `_flag_data_gap_once` (3264-3270), keyed on (parcel_id, description).
- reporting.py `_bts_use_text` / `_projection_summary` (511-582) show "yes
  (default setback; <review>)" when `bts_review_text` is set, else "yes,
  rated N/A (default setback ...)".
- `zoning_rules.json` today: all six jurisdictions draft; every
  `unlisted_districts` is `"unknown"`; no present-but-unreadable setback
  value anywhere; every listed district has a `dimensions` block.

## Blast-radius facts the design must rest on

- **Unlisted districts reach site fit only where `unlisted_districts` is
  `"not_permitted"`.** With `"unknown"` (every jurisdiction today) an
  unlisted district classifies UNKNOWN, fails the S9 gate, gets no
  projection and keeps the acreage proxy. So with the rules as shipped, even
  with every jurisdiction verified, counting unlisted districts moves no
  score. It moves scores once a human sets `unlisted_districts:
  "not_permitted"` and verifies: then an unlisted district (typically
  residential R-*, AG*) is REZONING, S9 = 1, or 4 with a by-right verified
  parcel within 1 mile. BTS MODERATE needs S9 >= 4, so an unlisted parcel
  can reach BTS MODERATE (and gate 3) only with a rezoning precedent.
- **Unreadable values can sit on any class, including by-right.** A
  mistyped setback on an industrial M-1 (S9 = 10) would count with the
  25 ft default where the real setback may be 40-60 ft. This is the
  highest-consequence sub-case (D3U-03) — no such value exists today.
- Fairburn PD (case 3) keeps its 13-parcel effect from reviews/29; nothing
  changes for it if its text stays byte-identical (D3U-05).

## Register

| ID | Sev | Concern | Mitigation / required design | Gate |
|---|---|---|---|---|
| D3U-01 | blocking | **Scope creep into `dist is None`.** A "count everything that defaults" implementation would let case 1 (no provenance, jurisdiction or governing jurisdiction not in the rules, rules unavailable, no machine-readable district) reach BTS. These are not districts, and their `verified` is False today only because of how `District` is built. | Count ONLY cases 2 and 4 (plus 3, unchanged). Keep the `dist is None` and `RULES_UNAVAILABLE` returns untouched (still `verified=False`, no kind). The new eligibility condition must name the kinds explicitly, never "not from_rules". | Tests: each case-1 variant (no provenance, `college_park`-style jurisdiction absent, governing jurisdiction missing, rules unavailable, district None) with every jurisdiction verified: `meets_bts_footprint` False, `bts_review_text` None, no flag. `git diff` shows no change to the two early returns. |
| D3U-02 | blocking | **Draft must stay inert.** The new kinds must never set the BTS flag or the text while the jurisdiction (or, for FID, the governing one) is draft. | Gate every new kind on `dist.verified` exactly as `default_listed` is gated (`rules.verified and ...`), in `_base_constraints`, `_empty`, the Projection field and `bts_review_text`. | All-draft scoring byte-identical: the five `tests/*_golden_check.py` exit 0 on the draft halves; `DraftDifferentialTests` green; a draft unlisted and a draft unreadable projection carry no investigate text in constraints/notes/memo and pass nothing. FID parcel with FID verified but south_fulton draft: no kind. |
| D3U-03 | high | **Metric movement once verified (SR-15).** Unreadable values on a by-right district now count with a 25 ft default; unlisted not_permitted residential parcels can reach BTS MODERATE with a precedent. Both are what the operator asked for, so the risk is that it happens silently. | The flag (D3U-04) on every such projection; the memo line names the kind; docs say the first verification after this change needs a new run and baseline. The live count (D3U-11) reports how many parcels each kind moves. | Decision note states the new run + baseline requirement; reviews/22 and reviews/29 point here. |
| D3U-04 | blocking | **Flag text must say what the operator asked:** that the setback is a DEFAULT and that the district is UNLISTED or its values UNREADABLE, so the operator investigates. Today's single template says "no district setbacks in zoning_rules.json", which is false for unreadable values and misleading for unlisted. | One stable text per kind, from `site_fit` only, with no dates, no parcel data and no district name (the constraints line already names it; a name would make the dedup key change with a rules edit). Suggested: unlisted "investigate further: district not listed in zoning_rules.json; 25 ft default setback used"; unreadable "investigate further: district setbacks unreadable in zoning_rules.json; 25 ft default used" (formatted with the actual default, like today). Exact strings are the coder's call; each must contain "default" and "not listed"/"unreadable". | Tests pin each exact text, its presence once in constraints, once in notes, once as a `data_gap` flag on both the per-parcel and the cycle path, and its absence for every other kind. |
| D3U-05 | blocking | **The existing Fairburn PD text and behaviour must not change.** Open `data_gap` flags dedup on (parcel_id, description); changing `BTS_REVIEW_TEMPLATE`'s output would write a second open flag for every PD parcel already flagged, and break reviews/29 pins. | Keep `BTS_REVIEW_TEMPLATE` and its formatted output byte-identical for case 3; keep `setback_default_listed` meaning "listed, all absent". Add the new kinds beside it (e.g. a separate field naming the kind), not by widening `setback_default_listed`. | reviews/29 tests (`test_verified_listed_district_without_setbacks_counts_with_the_review_text`, `D3ScoringTests` PD half) green unchanged. |
| D3U-06 | high | **Listed-without-dimensions vs unlisted (case 2 ambiguity).** `zoning.district_for_stored` returns `dims=None` for both, so the code would tell the operator "not listed" for a district that IS listed (no such district today, but the schema allows it). | Either (a) `zoning.District` gains a field saying whether the district was found (default keeps existing constructors valid), and site_fit treats "listed, no dimensions" as UNREADABLE; or (b) site_fit words case 2 as "not listed in zoning_rules.json or listed without dimensions". (a) is preferred but touches `zoning.py` (protected, Tier 2, zoning golden check must stay identical). The coder states which. | Test with a listed district whose `dimensions` is absent / not a mapping: text and kind as designed; zoning golden check exit 0. |
| D3U-07 | high | **Partial unreadable (case 5) is untouched by the requirement but is the same data error.** Today it counts with the max of the readable keys and no flag; the setback used may understate the real one (e.g. front 40 read, side "see table"). | Counting stays as it is (the operator was not asked about it; changing it is out of scope). Recommended: also write an investigate flag naming that a district setback was used and some values were unreadable, without changing `meets_bts_footprint`. If not adopted, record it as a residual for the operator in `03_reviewer_decision.md`. | Either a test pinning the flag with BTS unchanged, or the residual recorded. Case 5 BTS result must be byte-identical either way. |
| D3U-08 | high | **Forgery guard (SR-16, reviews/22 finding 3).** A new Projection field that `meets_bts_footprint` trusts must keep the `_ISSUED` identity check; `copy`, `dataclasses.replace`, direct construction and a draft-made-to-look-verified must still fail for every new kind. | Keep the `type(p) is Projection and _ISSUED.get(id(p)) is p` check first in both `meets_bts_footprint` and `bts_review_text`. No new public site_fit name used by research.py (the static guard's `ALLOWED` set stays as it is); research gets every text through `bts_review_text`. | Extend `test_a_default_setback_projection_built_outside_the_module_never_passes` to each new kind; `StaticResearchTests` unchanged and green. |
| D3U-09 | should-fix | **The flag's suggested resolution.** research.py's text "check the district's setbacks in the code by hand; a human adds them to the rules file between runs (reviews/29_operator_decisions)" does not fit an unlisted district (the human must add the district: use status and dimensions). | One resolution text that covers all three kinds (resolution is not part of the dedup key, so changing it writes no duplicate flag), or a resolution chosen from `bts_review_text`'s output without a new site_fit name. Cite this folder. | Test the resolution for each kind. |
| D3U-10 | should-fix | **Tests that invert deliberately.** `test_site_fit.test_d3_unlisted_district_stays_bts_na`, `test_d3_unreadable_setback_value_stays_bts_na`, `test_operator_decisions.D3ScoringTests.test_unlisted_district_stays_na`, `test_memo_lines` (unlisted "rated N/A"), and the finding-6 half of `test_bts_threshold_boundary` all pin today's narrow reading. | Rename and invert them with a comment naming this folder; keep each one's still-valid half (case 1 never counts; draft never counts; 149,999 SF still fails but is still flagged). Do not delete a test to make the suite pass. | Root suite green with a count that only grows; every inverted test fails on the base code (check with the code files stashed). |
| D3U-11 | should-fix | **Live evidence must show the real blast radius, counts only (SR-8).** | Throwaway local DB only (`postgresql://postgres:testpw@localhost:55432/lr_d3`), rules patched in memory only (`tests/zoning_helpers.patch_rules`), never written to a file. Report per jurisdiction: (A) shipped rules, every jurisdiction verified in memory: parcels newly counted (expected 0, since `unlisted_districts` is `"unknown"`); (B) the same with `unlisted_districts: "not_permitted"` in memory: newly projected, newly >= 150K SF, newly BTS MODERATE, flags by kind. If the local corridor copy is not available in lr_d3, say so and report fixture counts instead; never use any other database. | Counts in `02_code_writer_response.md`; no parcel ids, owners or addresses. |
| D3U-12 | blocking | **Golden checks change; no record may be regenerated.** The adjacency golden case projects every fixture parcel with all four fixture jurisdictions verified; fulton-Z03 (South Fulton R-3) and fulton-Z05 (Fairburn R-2 OVERLAY) are UNLISTED, so they gain the investigate text even though neither reaches 150K SF (135,200 SF each) and neither passes the S9 gate in scoring. | Produce an expected-diff list and make the check assert exactly it (an allow-list of (state, section, parcel, field) with the exact new strings), not a regenerated JSON. See "Expected golden diff" below. | All five golden checks exit 0 after the change, the adjacency one through the explicit expected-diff list; the record files under `reviews/*/golden_*.json` show an empty `git diff`. |
| D3U-13 | should-fix | **Reporting branches.** After the change, the "yes, rated N/A (default setback ...)" branch is reachable only for a projection that is used for BTS but has no kind (should be none, since case 1 never passes the S9 gate). | Keep the branch as a fail-closed fallback with a test that reaches it (a used projection with rules from case 1, or a forged one), so its text stays covered. | Test present. |
| D3U-14 | nit | **No lot coverage cap for unlisted districts.** An unlisted district has no dimensions, so no coverage cap or FAR applies on top of the 25 ft default: the projection is the most generous one possible. | Keep the existing "no lot coverage cap in the rules" constraint line; mention in the design that the investigate text covers it. | doc check |
| D3U-15 | nit | **Flag volume.** Flags are written whatever the size (reviews/29 design). With `"not_permitted"` set, every S9-passing unlisted candidate parcel gets one. | Accept (operator asked to flag); report the count in D3U-11. | counts in 02 |

### Expected golden diff (reviewer's prediction — the code writer must confirm against the real output)

| Check | Expected after the change |
|---|---|
| `tests/zoning_golden_check.py` | identical (exit 0): Z03/Z05 fail the S9 gate (`unlisted_districts` "unknown"), no projection is read in scoring |
| `tests/site_fit_golden_check.py` | identical (exit 0), also with South Fulton verified: every fixture parcel's district is listed with numeric setbacks or has no zoning |
| `tests/hazards_golden_check.py` | identical (exit 0) |
| `tests/building_comps_golden_check.py` | identical (exit 0) |
| `tests/adjacency_golden_check.py`, gate off and gate on + empty table | `verified.scoring`, `verified.executed`, `verified.footnote` and all of `draft` identical. `verified.projections[fulton-Z03, fulton-Z05].constraints`: one added line, the unlisted investigate text, right after the "setback 25 ft all sides = default (district ... not listed in the rules), verified jurisdiction, default setback" line. `verified.memo[fulton-Z03, fulton-Z05].summary_used`: "used for BTS: yes, rated N/A (default setback)" becomes "used for BTS: yes (default setback; <unlisted text>)". `verified.memo[...].development_potential`: the "Constraints applied" line gains the same text; its "used for BTS: no (verified jurisdiction, default setback)" stays (the S9 gate is unchanged). `summary_not_used`: identical. No other parcel (Z07 east_point and Z09 fid stay draft in this case; Z06/Z08/Z11 have no district rule) changes. Any other field changing for Z03/Z05 (setback_source wording, rules_label) is an extra diff the writer must justify or avoid. |

## Standing risks

- SR-1: no human-owned file touched (`zoning_rules.json`,
  `building_prototypes.json`, `prepare.py`, `parameters.json`,
  `sources.json`, `program.md`, `rules_sources.json`); `site_fit.py`,
  `research.py`, `reporting.py` (and `zoning.py` if D3U-06 (a)) changed
  between runs only: no open run. `test_zz_human_files_unchanged` green.
- SR-2: no new SQL expected; any is a module constant.
- SR-6: every new path runs on fakes.
- SR-8: live evidence counts only.
- SR-14: no new `action_type` (`data_gap` exists).
- SR-15: BTS is a metric input once verified: new run + baseline before the
  first verification (D3U-03).
- SR-16: `_ISSUED` guard kept (D3U-08).
- SR-3/4/5/7/9/10/11/12/13: no new surface.

## Go / no-go gates (all must hold before commit)

1. Only cases 2 (unlisted) and 4 (no readable value, at least one
   unreadable) newly count; case 1 never; case 3 byte-identical; case 5
   counting unchanged (D3U-01, -05, -07).
2. Nothing changes while draft (D3U-02).
3. Each new kind has its own stable text containing "default" and
   "not listed"/"unreadable", written once as a `data_gap`, never a block,
   on both scoring paths (D3U-04).
4. The forgery guard and research's allowed site_fit names are unchanged
   (D3U-08).
5. Root suite (>= 1,744 at base) and `screener/tests` (134) green;
   inverted tests fail on the base code (D3U-10).
6. Golden checks: four identical, adjacency through an explicit
   expected-diff list matching the table above (or a justified deviation),
   no record regenerated (D3U-12).
7. Docs: `reviews/29_operator_decisions/03_decision_note.md` and
   `reviews/22_site_fit/03_decision_note.md` point here and close D3-01 /
   D3-02 with the operator's answer; the site_fit module docstring and
   research/reporting docstrings say unlisted and unreadable count; the
   decision note says the first verification after this needs a new run
   and baseline.
8. `git diff` of `hazards.py`, `prepare.py`, `zoning_rules.json` and
   `screener/` empty in this change (operator answers 1/D2, 4, Step 2 are
   other rounds).

**Verdict: GO with these gates.** No blocking concern lacks a mitigation;
the blocking items are design constraints the code writer must meet, not
reasons to stop.
