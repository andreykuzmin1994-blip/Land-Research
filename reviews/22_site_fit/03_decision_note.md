# 03 — Site fit: decision note

> **Tier 2.** Design `00_design.md`; pre-change register and dispositions
> `02_risk_review.md`. This note records the decisions the change rests
> on, the independent **adversarial** post-change review (fresh contexts
> that did not write the code; each finding was challenged by a separate
> skeptic pass and probed on a live local PostGIS with real Fulton corridor
> parcels, reporting counts only), what changed after it, and the
> evidence. Model names are omitted, as in `reviews/21_zoning/`.

## Change

- **`building_prototypes.json`** (new, human-owned, never edited by the
  agent): the operator's SiteFit defaults (cross-dock 1,000 x 520 ft,
  front/rear-load 640 x 260 ft, courts, trailer parking, dock spacing,
  end clearance, 40 ft shrink step and minimum lengths), `source`
  "operator SiteFit tool defaults", `as_of` 2026-11-05. Frozen by SHA-256
  at load; `runner.evaluate` refuses an experiment while it differs from
  git HEAD; listed in every protected-file list.
- **`site_fit.py`** (new, protected, immutable during a run, imports
  neither research nor runner): inward setback buffer in PostGIS (SRID
  2240 feet), rectangle fitting in pure Python, the largest building per
  prototype ("shrink to fit"), docks, trailer stalls, coverage and the
  constraints applied, stated on every projection.
- **`research.py`** (between-runs edit): BTS uses the projected max
  building (>= 150K SF) only for parcels passing the S9 gate (verified
  jurisdiction); every other parcel keeps the 8.61 ac proxy exactly.
- **`reporting.py`, `runner.py`, `cli.py`** (protected / ops): the
  snapshot's Development Potential section and one memo line per
  highlight, the committed-file check, `make site-fit` with SiteFit study
  export.

## Decisions

1. **Scope of the scoring switch (SF-02).** Only `_assess_strategy_bts`
   changes, and only for parcels that pass the S9 gate:
   `zoning.classify_stored(...)` is `verified` AND the class is by_right,
   conditional or rezoning (`research._zoning_verified`). For those, the
   program.md "accommodates >= 150K SF footprint" test is
   `site_fit.meets_bts_footprint(projection)` (largest fitted prototype
   >= 150,000 SF); anything else (no geometry, error, prototypes
   unavailable, nothing fits) is BTS N/A, never the acreage proxy. Every
   other parcel keeps the 8.61 ac proxy exactly. **Ground lease keeps the
   acreage proxy for every parcel.** No other strategy, sub-score or gate
   changes.
2. **Flipping a jurisdiction to verified now changes S9 AND BTS
   together.** Like any `zoning_rules.json` verification it needs a new
   run and baseline. Once anything is verified, a change to
   `building_prototypes.json` or `site_fit.py` (algorithm, angle grid,
   tolerance) and a PostGIS/GEOS upgrade can move BTS ratings, so they
   are metric changes: between runs only, then a new run and baseline
   (SF-20).
3. **Fit rule (SF-08).** The full envelope (building L x W plus truck
   court and trailer parking per dock side) must lie inside the
   setback-buffered area. This is stricter than SiteFit's own check,
   which tests only the building against the setback line; truck drives
   and circulation are not modelled either way. Lattice 40 ft; minimum
   length 600 ft cross-dock (L >= W), 400 ft front/rear (104,000 SF).
4. **Setback rule (SF-09).** Uniform setback = max(non-null front, side,
   rear) of the district on every side; a known district with none of the
   three numeric, an unlisted district, or no zoning gets the 25 ft
   default, labelled. Buffer keys are never folded in. A non-numeric
   value is reported, not guessed. **After review (finding 6):** a
   default setback is never a BTS input: in a verified jurisdiction it is
   labelled "verified jurisdiction, default setback" and
   `meets_bts_footprint` is False (BTS N/A).
5. **Adjacency buffers are not applied (SF-10)**: the database stores only
   candidate parcels, so neighbour zoning is incomplete and a missing
   neighbour does not mean a non-residential one. Every projection says
   so, and **after review (findings 2, 11)** names each buffer the
   district's rules list, with its value (Fairburn M-1's 50 ft buffer
   toward any other district included).
6. **Coverage (SF-11)** = building SF / gross geography area of the
   parcel (never assessor acreage). A cap steps the length down on the
   lattice. A FAR of 1.0 or more is not a cap (finding 12). Height is not
   checked; every projection says so, with the district maximum when the
   rules have one (finding 13).
7. **The projection cannot be steered from research.py (SF-03).** The
   public API takes `(conn, parcel_ids)` only; the module reads geometry,
   zoning and prototypes itself; the result is a frozen dataclass; the
   150K threshold and the test live in `site_fit`. Static tests pin that
   research.py uses only `project_parcel`, `project_parcels`,
   `meets_bts_footprint`, `data_gap_text` and
   `verify_prototypes_unchanged`, assigns nothing to `site_fit.*` or to a
   result field, reads no result field, passes no extra arguments and
   never names the prototypes file. **After review (findings 1, 3):**
   `tests/static_guards.py` makes `site_fit.<allowed name>` after one
   plain `import site_fit` the ONLY way research.py reaches the module
   (no alias, no from-import, no bare reference, no `getattr` with a
   computed or protected name, no `importlib` / `sys.modules` /
   `__dict__` / `__import__` / `eval` / `exec`, no import of a module that
   re-exports it, no name `Projection` / `PrototypeFit` / private state);
   the same guard now covers `zoning`. And `meets_bts_footprint` accepts
   only a Projection the module itself built (an identity registry with
   weak values), so a Projection constructed, copied or
   `dataclasses.replace`d elsewhere never passes. **Runtime
   monkeypatching that evades both remains SR-16 (accepted)**: it needs a
   drifting agent and is visible in the `exp:` diff.
8. **Car parking is not modelled (SF-21)**; no stall count is derived
   from leftover area. **Stormwater detention is not deducted** either
   (finding 7: SiteFit's default 400 x 200 ft pond is not placed); every
   projection, the memo footnote and the study notes say so.
9. **The operator command does not refuse a non-local database
   (deviation from SF-18).** The operator's real database is the hosted
   one, so refusing it would make the command useless. Instead it
   refuses without `DATABASE_URL` (exit 2, the `zoning_import`
   pattern), sets the session read-only, selects only parcel id,
   geometry, zoning and scores, never prints owner names or addresses,
   and writes only to the gitignored `site_fit_studies/`. It needs no
   run-state refusal because it writes nothing shared.
10. **The fitted length is a lower bound (finding 4).** The slab sweep
    tries every vertex event plus a fixed grid of 64 steps over the
    feasible range, each checked exactly; an optimum between those
    positions can be missed by at most one grid step. The grid is a
    superset of the old candidates, so no fit got shorter.
11. **Work per parcel is bounded (finding 5).** A buffered ring above 400
    vertices is fitted on `ST_SimplifyPreserveTopology` (1 ft) of the
    parcel buffered by setback + 2 ft (every point of that
    simplification is within 2 ft of the deeper buffer, so it stays
    inside the setback line; stated on the projection); above 600
    vertices the projection is `error` ("too complex"), which a verified
    parcel reports as BTS N/A plus one data_gap flag.

## Post-change review verdict: approve-with-fixes (1 high, 7 should-fix, 7 nits; all fixed except one no-action nit)

| # | Severity | Finding | Resolution |
|---|----------|---------|------------|
| 3 | high | research.py could build a `Projection` directly (`from site_fit import Projection`) that `meets_bts_footprint` accepted; the static scan saw only `site_fit.<attr>`. Reproduced. | `meets_bts_footprint` accepts only projections in the module's identity registry (`_ISSUED`, weak values, set by `_issue` in `_empty` / `_project_row`); a constructed, copied, replaced or `type(p)(...)`-rebuilt object fails (`test_a_projection_built_outside_the_module_never_passes`, and the reviewer's exact `_inflate` repro executed in `test_a_forged_projection_never_passes_at_runtime`). Static guard below. |
| 1 | should-fix | The static scan missed aliases (`import site_fit as _sf; _sf.BTS_MIN_FOOTPRINT_SF = 1` passed all six static tests), from-imports, `importlib`, `sys.modules`, computed `getattr`. Same pattern in the zoning scan. Reproduced. | `tests/static_guards.module_reference_problems`: exactly one plain `import site_fit`; no ImportFrom of it; the bare name only as `site_fit.<allowed>`; no `.site_fit` attribute, no import of a re-exporting module (reporting, runner, cli, zoning_import); no `importlib`/`gc`/`inspect`/`ctypes`/`builtins` import; no `__import__`/`eval`/`exec`/`compile`/`globals`/`vars`/`locals`; no `__dict__`/`__globals__`/`modules`/... attribute; `getattr`-family only with a constant, non-protected, non-dunder name; no `Projection`/`PrototypeFit`/private-state names. 15 reviewer-style repros each fail it (`test_the_reviewer_repros_fail_the_guard`); research.py passes. Applied to `zoning` with every public name allowed and the provenance encoders banned (`test_research_reaches_zoning_only_as_zoning_dot_public_name`, 9 repros). |
| 6 | should-fix | A verified jurisdiction's district with no setbacks (Fairburn PD, an unlisted district) got the 25 ft default labelled "verified rules" and used for BTS. | `_Rules.from_rules` / `Projection.setback_from_rules`; label "verified jurisdiction, default setback"; `meets_bts_footprint` requires a rules-derived setback; memo/snapshot say "used for BTS: yes, rated N/A (default setback ...)". Test: Fairburn PD verified, 520,000 SF fitted, BTS False; Fairburn M-1 verified passes. |
| 7 | should-fix | Stormwater detention (a SiteFit default) was left out without disclosure. | `site_fit.DETENTION` in `NOT_MODELLED`, on every projection (every status, `unavailable` included), the memo footnote and the study notes; README and `00_design.md` "Not modelled" list it. |
| 8 | should-fix | The memo highlight said flood/wetlands only, not buffers. | Each memo line now carries the uniform setback and "adjacency buffers not applied"; one footnote under the highlights lists buffers, flood/wetlands, detention, car parking, the fit rule and height (`test_memo_footnote_lists_what_projections_leave_out`, `test_memo_line`). |
| 4 | should-fix | Slab candidates only at vertex events missed interior optima (rhombus: 720 instead of 800). Conservative. Reproduced. | `_candidate_ys`: events plus a 64-step grid; the rhombus now gives 800 (`test_v_sided_rhombus_interior_optimum`, brute-force checked). Documented as a lower bound (decision 10). |
| 5 | should-fix | No complexity bound: runtime about quadratic in vertices (1,000 vertices 12.8 s). | Decision 11. Offline: a 1,000-vertex no-fit strip is refused in under a second (`test_too_complex_ring_is_an_error_not_an_unbounded_search`); SQL and parameters pinned. Live: a 1,000-vertex wavy edge is simplified, projected in 0.1 s and every placement is covered by the unsimplified buffer (`live_site_fit_check.py` (e)). |
| 2 | should-fix (skeptic: minor) | Fairburn's 50 ft buffer toward any other district was left out while the text named residential buffers only. | Not folded in (it still depends on neighbour zoning; SF-10 stands), but named: "buffers listed for fairburn M-1, not applied: buffer_abutting_other_districts 50 ft, buffer_along_all_public_ROW 25 ft (within the 35 ft setback), side_rear_yard_abutting_residential 50 ft" (`test_every_listed_buffer_is_named_not_applied`). |
| 9 | nit | The memo's "used for BTS" re-implements research's gate. | Kept (protected code), plus `test_used_for_bts_label_matches_the_scoring_gate` over every listed district + unlisted/unknown codes, raw and blank zoning, under all-draft, all-verified and mixed rules. |
| 10 | nit | Pre-existing: geometry/acreage are agent-written via discovery; runner checks the two data files, not the protected .py files; autopilot never runs the suite. | No action in this change (as the finding says). Left for the human: a `git diff --quiet HEAD -- <protected .py>` refusal in `runner.evaluate` and a suite run in autopilot, between runs. |
| 11 | nit | "neighbour zoning not in DB" is wrong once zoning_import has run. | Reworded (finding 2's text); design note and SF-10 row updated. |
| 12 | nit | Atlanta FAR 2.0 became a "200%" cap; a rules load failure read "no district rule". | FAR >= 1.0 is a note ("does not limit a one-story footprint"), not a cap; "default (zoning rules unavailable: ...)" with label "zoning rules unavailable" (`test_rules_unavailable_is_named`). |
| 13 | nit | README said height is stated on every projection; it was not on the default path. | Height note on every rules path ("no district maximum in the rules" when none) (`test_height_note_on_every_rules_path`); README reworded. |
| 14 | nit | The CLI named a SiteFit menu path that does not exist. | "open it in SiteFit: Studies > Open file…" (pinned in the CLI test); README too. |
| 15 | nit | The fit rule is stricter than SiteFit's; not said in the memo. | `FIT_RULE` now says "stricter than SiteFit, which lets courts and trailer parking sit in the setback"; README explains why a SiteFit refinement may hold a longer building. |

No finding was refuted. The skeptic passes narrowed finding 2 (the 25 ft
ROW buffer sits inside the 35 ft setback, and the 50 ft buffer still
depends on neighbour zoning, so the defect was the disclosure) and noted
that findings 4 and 5 are conservative / not failing on today's corpus.

## What the reviewers verified

Beyond the findings: the draft-equals-28d3205 proof (offline golden record
and the live byte-identical scoring run), SR-1 lists and the runner's
committed-file check order, the SiteFit formula ports and hand goldens,
units (SRID 2240 vs geography), containment of every reported envelope
in PostGIS's own buffer, savepoint recovery, PII in the CLI and export,
and that SiteFit.html is not committed. They reproduced findings 1, 3, 4
and 5 in throwaway copies without touching repo files.

## Evidence (live, local PostGIS 3.4 / GEOS 3.9)

- **research.py is unchanged by the fixes**, so the draft-mode metric
  argument stands: `tests/site_fit_golden_check.py` is identical to the
  28d3205 record (22 parcels + the cycle: results, executed SQL and
  parameters, inserts, notes; with South Fulton verified the 14 parcels
  of other jurisdictions are identical) and `tests/zoning_golden_check.py`
  is identical to the ba4a780 record. The earlier live run (1,075
  `parcel_scores` rows byte-identical to 28d3205,
  `actionable_pipeline_count` 15 both ways) is unaffected: draft parcels
  never request a projection.
- **Suites:** `python -m unittest discover -s tests` 1,165 OK (1,150
  before the fixes); `python -m unittest discover -s screener/tests -t .`
  134 OK.
- **`tests/live_site_fit_check.py`** synthetic: 13/13 OK, including the
  new (e) 1,000-vertex simplification case (0.1 s, covered by the
  unsimplified buffer). Own database, dropped.
- **Corpus** (own copy of the 1,075 corridor parcels with imported
  zoning, all rules draft; counts only): ok 1,065, empty after setbacks
  10; cross-dock full size 21, only after shrinking 74; front/rear full
  size 271, only after shrinking 150; no prototype 644; median max
  building 166,400 SF; acreage proxy vs projection: both 293, proxy only
  313, projection only 1, neither 468; all 937 envelopes covered by
  PostGIS's own buffer; cached re-projection identical. Against the
  pre-fix module on the same copy: 3,194 prototype fits identical, 1
  longer (the grid found an interior optimum), 0 shorter. Runtime 27.9 s
  for all 1,075 (pre-fix module 17.7 s on the same copy and machine): the
  grid's cost, paid only for verified parcels in scoring and the top 10 in
  the memo.
- **Informational, every jurisdiction verified** (in-process, nothing
  written): 457 parcels pass the S9 gate; 30 of them have a default
  setback (13 of which fit >= 150K SF and are now BTS N/A instead of
  passing); 100 pass the BTS footprint test.

## Behaviour a human will notice

- Every projection's constraint list is longer: the adjacency-buffer
  sentence, the named buffers of the district, a height note even
  without a district rule, the detention line, and the fit rule's
  "stricter than SiteFit" clause.
- Memo highlights show the uniform setback and "adjacency buffers not
  applied", with one footnote below them.
- A verified jurisdiction's district without setbacks (or an unlisted
  district) shows "verified jurisdiction, default setback" and BTS N/A.
- Atlanta's FAR 2.0 no longer shows as a 200% cap.
- A few projections may report a longer building than before (never
  shorter); the projection of all 1,075 parcels takes about 28 s instead
  of 18 s.

## What the operator must do

- Nothing for the current run: all rules are draft, so the metric is
  unchanged.
- Before verifying any jurisdiction: decide whether a default setback in
  a verified jurisdiction should stay BTS N/A (this change) or whether the
  district needs its own setback values in `zoning_rules.json`.
- Once anything is verified, treat any change to `site_fit.py` or
  `building_prototypes.json` (including this one) as a metric change:
  between runs, then a new run and baseline.
- Optional, between runs (finding 10): have `runner.evaluate` refuse when
  a protected `.py` file differs from HEAD, and run the static suites from
  autopilot before each experiment.

## Operator decision (2026-11-09): districts without setbacks do not block

Recorded in `reviews/29_operator_decisions/` (decision D3). The operator's
answer to "Before verifying any jurisdiction: decide whether a default
setback in a verified jurisdiction should stay BTS N/A" above: **"if no set
backs do not block, we will just investigate further"**.

- Changed: in a verified jurisdiction, a district LISTED in
  `zoning_rules.json` whose front/side/rear setbacks are all absent or null
  (today only Fairburn PD) is projected with the 25 ft default, and that
  projection now counts for the >= 150K SF BTS footprint test
  (`meets_bts_footprint`). The projection, the scoring notes and the memo
  carry "investigate further: no district setbacks in zoning_rules.json;
  25 ft default used" (`site_fit.bts_review_text`), and scoring writes it
  once as a `data_gap` flag, never an `actionability_block`.
- Kept (finding 6 still applies): an UNLISTED district (no dimensions in
  the rules) and a listed district with a present but unreadable setback
  value stay BTS N/A. The label `verified jurisdiction, default setback`,
  the zoning gate, the `_ISSUED` forgery guard and every static guard but
  one new allowed name (`bts_review_text`) are unchanged.
- While every jurisdiction is draft, scoring is byte-identical (golden
  checks). The first jurisdiction verification after this change needs a
  new run and baseline, as before; it now moves BTS for Fairburn PD too.

**Open for the operator (D3-01, review round 2, 2026-11-09).** The
operator's words also fit an UNLISTED district in a verified jurisdiction
(it has no setbacks in `zoning_rules.json` either) and a listed district
with an unreadable value; both still stay BTS N/A without the investigate
text. Fact to decide with: the only parcels D3 moves into BTS today are the
13 Fairburn PD parcels (of 30 projected) that pass the 150K SF test, and
`zoning_rules.json` lists Fairburn PD `warehouse_distribution` as
`not_permitted` ("Fairburn PDs are mixed-residential"): they pass S9 only as
REZONING, the same kind of parcel the listed-only reading was meant to keep
out. Answer before the first verification; either way the change is
between runs and needs a new run and baseline.

**Answered (operator, in chat, 2026-11-09): "Count them" (D3-01 and D3-02
CLOSED, `reviews/30_d3_count_unlisted/`).** In a verified jurisdiction an
UNLISTED district and a listed district with no readable front/side/rear
value now also count for the >= 150K SF test with the 25 ft default, never
block, and carry "investigate further: district not listed in
zoning_rules.json; 25 ft default setback used" or "investigate further:
district setbacks unreadable in zoning_rules.json; 25 ft default setback
used", written once as a `data_gap`. Finding 6 still holds for a parcel with
no district rule at all (no stored zoning, jurisdiction not in the rules) and
for rules unavailable. The first verification after this change needs a new
run and baseline.
