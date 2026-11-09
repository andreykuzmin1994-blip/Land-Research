# 03 — F8 earthwork: reviewer decision (Agent 3)

> Fresh context. I did not write this code. I read the requirement, the risk
> review `01_risk_review.md`, the code writer response
> `02_code_writer_response.md` and the actual diff
> (`git diff 4fe8080..HEAD`, commits `35cd3dc`, `72ff802`, `51457e9`), then
> re-ran every check myself. Model names are omitted.

## Operator answers (2026-11-09, in chat, binding), as honoured here

1. **D3:** COUNT unlisted districts AND districts with unreadable setback
   values (answers D3-01 / D3-02). Root D3 round; nothing in this folder.
2. **D2 flood scope:** "just review". D2 stays as implemented (V/VE
   review-only, floodway still blocks). No flood-rule change. This diff
   touches no flood code.
3. **Null-beats-low:** NOT answered. No new root sub-score. This diff adds
   none, and the screener estimate feeds no score either.
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

| Check | Result |
|---|---|
| Files changed | `screener/checks.py`, `run.py`, `report.py`, `README.md`, new `tests/test_earthwork.py`, this folder. `git diff --stat 4fe8080` over `screens.py`, `autoresearch/`, `settings.toml`, `rank.py`, `scoring.py`, `CLAUDE.md`, `prepare.py` is empty. No human-owned or eval file changed. |
| Leak into scoring (F8-1, G1) | Structural guard holds. `slope_checks` returns the estimates in input order from `pool.map` (no worker writes shared state). `run.main` keeps them in a dict keyed by `id(p)`, which is stable because every survivor object stays referenced (in `kept` or `rejected`) for the whole run. They are attached only after the score/flag loop, the sort and the rank. The spy tests wrap `learned_score`, `fixed_score`, `site_rejects` and `flags`, and assert that all four ran and that none saw an earthwork key. They run with slope on, `--no-slope` and a failed check (as first written; post-change round 1 below added the helper-None, `slope = false` and score-independence runs). |
| Helper never raises (F8-2, G3) | `_slope_from` / `_slope_problem` are byte-identical to base. The earthwork call sits after `_slope_from` inside a `try` over the same exception tuple plus `ZeroDivisionError` / `OverflowError`. `slope_area_m2(geom)` can't add a new crash: `too_small_for_slope` has already called it on the same geometry in `work()`. 18 unreadable-histogram cases and 8 bad area/pixel cases return None. |
| Maths (F8-4..6, G4) | I re-derived the `GOOD_SLOPE` value by hand: mean 256 m and MAD 2.5 m give 1.25 m³/m² and 8.2 ft. Cut = fill at the histogram mean. The value is linear in area. 1 yd³ = 0.9144³ m³ exactly. `FT_PER_M` is reused from `geo`. The nominal pixel area is 100·cos²(lat). The coverage band [0.5, 2] rejected no parcel in the writer's live sample. |
| Null paths (F8-7, G5) | Each of skipped, failed, `--no-slope`, `slope = false` and helper None gives an empty cell and a brief that reads "not computed (reason)". Flat ground gives `0` / `0.0`, not empty. The brief's fallback order matches `_slope_problem`, which refuses a None `steep_pct`, so "slope not checked" can't mislabel a computed slope. |
| Requests / deps (F8-9, G6) | One slope request per parcel, 0 when skipped or slope is off. `requirements.txt` and settings are unchanged. |
| Eval (F8-10, G7) | I ran `python -m screener.autoresearch.evaluate` at HEAD and on a `git archive 4fe8080 screener` copy in scratch. The line is identical: `pool 2229, positives 34, k 45, hits 2, recall 0.059, positives_screened_out 1, score_range [6.5, 85.0], nonfinite 0`. `rank_import_problems() == []`. |
| Schema (F8-8) | `earthwork_cy` and `mean_cut_fill_ft` sit right after `relief_ft` in `ranked.csv` / geojson / map. `rejected.csv`'s header is unchanged. No reader in the repo uses column positions. Operator spreadsheets keyed on column letters shift by two. Accepted and documented. |
| Wording (F8-3, G9) | Two significant figures and 0.1 ft. The brief names "not a grading plan", drainage slope, terracing, shrink/swell, topsoil and rock. There is no dollar figure, flag or "why it ranks here" entry. The README covers the meaning, the caveat, the stability note and the guard limit. |
| Writer deviations 1–5 | Accepted. The `pixel_m2` argument is required for the F8-4 cross-check. The `EARTH_SLOPE` fixture is the correct response to that check. 2 significant figures is within G9. Each changed return type has one caller. The wider static guard is fine. |

## Fix I made

**R-1 (LOW, test quality).** The static guard's self-test
(`test_the_guard_catches_a_named_read`) re-implemented a constant-only
check of its own. That check was narrower than the real guard, so it never
exercised the real guard. I moved the guard's rule into one helper,
`named_estimate(tree)`, and both tests now use it. The self-test now covers:

- a string-constant read;
- a mixed-case read;
- an attribute read (`checks.EARTHWORK_KEYS`);
- a local name;
- the documented limit: a runtime-built string, `"earth" + "work_cy"`, gets past the guard. Pinning it keeps the docstring honest.

Production code did not change.

## Open items (not blocking)

- The guard limit (SR-16 analog) is unchanged. The spy tests live in
  `screener/tests`, and the loop's own `evaluate.py` doesn't run them. The
  guard holds only because the loop edits `rank.py` alone and every `exp:`
  diff gets a human read. The README says so.
- Feeding earthwork into the fixed `topography` part, or into any score, is
  a later human decision and would need eval data that doesn't exist.

## Tests

- Root: `python -m unittest discover -s tests` gives **1744 OK** (base 1744).
- Screener: `python -m unittest discover -s screener/tests -t .` gives
  **162 OK** (base 134, +28; after the R-1 fix the count is still 28 earthwork
  tests, with more subtests).
- I did not repeat the live run. The writer's single `--limit 30` run, which
  is counts only, is the sub-item's one allowed live check.

## Decision

**APPROVED after a small fix (R-1).** All gates G1–G9 hold, every
F8 risk is addressed, the eval line is identical and the operator answers
are honoured exactly.

Standing risks:

- SR-6: offline fakes.
- SR-8 analog: counts only.
- SR-10: no new dependencies.
- SR-11: no new requests.
- SR-15 / SR-16 analogs: the eval boundary, and the after-scoring
  attachment plus the human read of `exp:` diffs.

## Post-change adversarial review round 1

Operator answers (2026-11-09, recorded as given): D3 counts unlisted
districts and unreadable setback values; D2 flood scope "just review" (no
flood-rule change); the null-beats-low question is not answered, so no new
root sub-score; "fix" the `research.py` ArcGIS error-envelope and audit-path
bugs and do the D1-11 `zoning_import` round; the agent may apply the
reviews/26 hazards prepare-mutation; build the screener quick wins. None of
the fixes below touches an eval file, `rank.py`, `scoring.py`,
`settings.toml` or `requirements.txt`.

| # | Finding | Verdict | What changed |
|---|---|---|---|
| 1 | `_round_sig` truncates below the units place (0.96 gives 0, 1.5 gives 1), so a sub-yard estimate reads `0` and contradicts "0 means flat" | Real. Confirmed: `_round_sig(0.6)` and `_round_sig(0.96)` both gave 0. | `_round_sig` now caps the rounding position at the units place, so below 100 it rounds to the nearest whole yard (0.96 gives 1, 1.5 gives 2, 0.4 still 0). New tests `test_rounding_below_ten_rounds_not_truncates` (nine values from 0.2 to 99.6, int type) and `test_near_flat_sub_yard_estimate_is_not_zero` (0.78 cy gives 1). The README now says `0` means at most half a cubic yard. |
| 1b | `earthwork_cy` 4 next to `mean_cut_fill_ft` 0.0 | Real, but not a bug: the two columns measure different things. A few cubic yards over many acres is a depth of hundredths of an inch, which rounds to 0.0 ft. The finding's histogram (250.00 to 250.02 m, two equal bins, MAD 0.005 m) on 6,000 m² reads 20 cy / 0.0 ft, before and after the fix; the 4 cy in the finding comes from a smaller area. | README explains that `0.0` ft can sit next to a few cubic yards on a large near-flat parcel. No code change. |
| 2 | The coverage band [0.5, 2.0] lets up to half the open ground be NoData, and the volume is still taken over the full area, while the README implies a shown number covers the whole parcel | Real as a documentation gap. The behaviour is kept: the band is wide on purpose, because the nominal Web Mercator pixel area is approximate and edge pixels on small parcels count in full, so a tighter band would blank good small parcels. | The constant's comment, the `_balanced_cut` docstring and the README now say that a gap of up to half the open ground is filled by assuming it is like the ground seen, and that the estimate can miss a pit or mound in the gap. New test `test_partial_coverage_extrapolates_to_full_area` pins it: at 60% coverage the MAD is unchanged and the volume scales to the full area. |
| 3 | This document's G1 row overstated the spy coverage (helper-None runs never asserted the spy) | Real. | The row is corrected above. `assert_never_seen_by_scoring()` is now also called in `test_helper_none_is_empty_not_zero`, `test_slope_off_in_settings` and `test_scores_and_order_do_not_depend_on_the_estimate`. |

Adding the spy to the score-independence test exposed a harness artefact,
not a production leak: the test harness hands both `run.main` calls the same
parcel dicts, and the first run had already attached the estimate to them,
so the second run's `site_rejects` saw `mean_cut_fill_ft`. A real run calls
`fulton.fetch_parcels`, which builds fresh dicts every time, and runs
`main` once per process. The test now gives the second run a deep copy of
the original parcels, so it is a clean "no estimate" run. The failure also
shows the spy catches a key that reaches scoring.

Tests after round 1:

- Root: `python -m unittest discover -s tests` gives **1744 OK**.
- Screener: `python -m unittest discover -s screener/tests -t .` gives
  **165 OK** (162 before round 1, +3).

No live run was repeated: the fixes change only rounding below 100 cy,
comments, README text and tests.

**Round 1 decision: APPROVED after fixes.**
