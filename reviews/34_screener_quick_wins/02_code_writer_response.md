# 02 — Screener quick wins: code writer response (F8 earthwork)

> Response to `01_risk_review.md` § F8, written after the code. Each risk
> item has its disposition and the test that pins it. Tests are in
> `screener/tests/test_earthwork.py` unless named otherwise. Base `4fe8080`.
> No eval file, `rank.py` or `scoring.py` was touched.

## Operator answers this round (2026-11-09, in chat, binding)

Restated from `01_risk_review.md` so this response stands alone:

1. **D3 (D3-01 / D3-02):** COUNT unlisted districts AND districts with
   unreadable setback values too. Root D3 round, not this folder.
2. **D2 flood scope:** "just review". D2 stays as implemented (V/VE
   review-only, floodway still blocks); no flood-rule change.
3. **Null-beats-low:** not answered. No new root sub-score lands. F8 adds no
   score of any kind.
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
| `screener/checks.py` | `slope_stats` returns `(slope fields, earthwork or None)`. After `_slope_from` succeeds it calls the total helper `_hist_balanced_earthwork(hists[0], slope_area_m2(geom), _nominal_pixel_m2(geom))` inside a `try`. The helper wraps the unrounded `_balanced_cut`. `slope_checks` keeps the slope keys on `p` exactly as before and RETURNS the estimates as a list aligned with the input order. New: `M3_PER_CY`, `SLOPE_PIXEL_UNITS`, `EARTHWORK_COVERAGE`, `EARTHWORK_KEYS`, `earthwork_fields`. `_slope_from` and `_slope_problem` are byte-for-byte unchanged. |
| `screener/run.py` | Keeps the returned list in a dict keyed by `id(p)` (object identity, so non-unique parcel ids can't collide). Attaches `earthwork_cy` / `mean_cut_fill_ft` only after the score/flags loop, the sort and the rank numbering, just before the notes and `report.write_all`. With `--no-slope` or `slope = false` the dict is empty and both fields are None. |
| `screener/report.py` | `RANKED_COLUMNS` gains `earthwork_cy`, `mean_cut_fill_ft` right after `relief_ft`. The brief gets one "Earthwork" line after the slope line: the value with its caveat, or "not computed (reason)". `rejected.csv` is unchanged. |
| `screener/README.md` | An "Earthwork (rough cut/fill)" paragraph: column meanings, maths, caveat, null meanings, stability note, the guard and its limit. Plus the `ranked.csv` row and the data-sources row. |
| `screener/tests/test_earthwork.py` | New: 28 tests. |

## Risk dispositions

| ID | Sev | Disposition | Tests |
|---|---|---|---|
| F8-1 | HIGH | **Fixed structurally.** The estimate is never on `p` while `screens.site_rejects`, `screens.flags`, `scoring.fixed_score` or `rank.learned_score` run. `slope_checks` returns it from `work()` through `pool.map`, collected in the caller, and `run.main` attaches it after the sort. `rank.py` and `scoring.py` are unchanged, and earthwork goes into no part of the score. Guard (a): spies on all four functions record `set(p)` on every call in a full offline `run.main`, with slope on, with `--no-slope` and with a failed slope check. No key containing `earthwork` or `cut_fill` is ever seen, and all four functions are called. Guard (b): a static AST check. | `test_spy_slope_on`, `test_spy_no_slope`, `test_failed_slope_check_is_empty_not_zero`, `test_scores_and_order_do_not_depend_on_the_estimate`, `EarthworkStaticGuardTests` (2) |
| F8-2 | HIGH | **Fixed.** `_slope_from` and `_slope_problem` are unchanged, so earthwork never decides whether an answer is usable. The helper is total and returns None for every malformed input listed. It catches `AttributeError, IndexError, KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError`, checks `math.isfinite` on every intermediate and output, and `slope_stats` wraps the call again in the same tuple. | `test_unreadable_inputs` (18 histogram + 8 area/pixel cases), `test_band1_faults_leave_slope_fields_as_before`, `test_helper_errors_never_fail_the_slope_check`, `test_unreadable_histograms_still_fail_as_before`; the existing `SlopeTests` unchanged and green |
| F8-3 | HIGH | **Fixed.** The volume is rounded to 2 significant figures and `mean_cut_fill_ft` to 0.1 ft. The brief line reads "Earthwork (rough): about N cubic yards of cut balanced by the same fill, X ft average cut or fill, to level the open ground (USGS lidar 2018-19, 10 m grid); one flat pad at the mean elevation of the open ground, not a grading plan: ignores drainage slope, terracing, shrink/swell, topsoil and rock". No dollar figure, flag or `why_site` entry. The README carries the full caveat list (incl. footprint-only leveling and grading since the lidar). | `test_columns_and_brief`, `test_rounding` |
| F8-4 | MED | **Fixed as recommended.** The volume is `area × MAD / 2` (per-pixel area = area ÷ Σcounts), so it doesn't depend on the pixel-size estimate. Coverage cross-check: Σcounts × 100·cos²(lat) m² must lie within [0.5, 2] × area, else None. **Consequence:** the existing `GOOD_SLOPE` fixture (60 pixels on a 20-acre square, about 0.05×) correctly gives None. The run tests therefore use `EARTH_SLOPE`: the same 1:2:2:1 band-1 shape (so the same MAD of 2.5 m) and the same band-2 ratio (`steep_pct` still 3.3), with counts sized to the area. | `test_coverage_check` (both edges and both sides), `test_good_slope_is_unchanged_and_gives_no_estimate`, `test_helper_none_is_empty_not_zero` |
| F8-5 | MED | **Fixed.** Bin centres, with width `(max-min)/len(counts)` as in `_hist_quantile`. The balance elevation is the histogram mean (not `statistics[0].mean`), so cut equals fill by construction. The docstring states the half-bin bound. | `test_cut_equals_fill_at_the_histogram_mean` (fill and cut computed independently) |
| F8-6 | MED | **Fixed.** `M3_PER_CY = 0.764554857984` (exact, = 0.9144³), and ft uses `geo.FT_PER_M` (same object, no new literal). | `test_units` (an exact 10,000 cy case), `test_good_slope_hand_value` (MAD 2.5 m, 1.25 m³/m², 8.2 ft) |
| F8-7 | MED | **Fixed.** Every null path gives empty cells (never 0) and the brief text "Earthwork: not computed (…)". The paths: skipped (open ground under one lidar pixel), failed check, `--no-slope`, `slope = false` in settings, helper None. Flat ground gives 0 / 0.0, which is distinct from empty. | `test_skipped_parcel_is_empty_not_zero`, `test_failed_slope_check_is_empty_not_zero`, `test_spy_no_slope`, `test_slope_off_in_settings`, `test_helper_none_is_empty_not_zero`, `test_flat_ground_reads_zero_not_empty`, `test_flat_ground_is_zero` |
| F8-8 | MED | **Accepted, documented.** **`ranked.csv` schema change:** two columns, `earthwork_cy` and `mean_cut_fill_ft`, are inserted right after `relief_ft`. Every later column moves two places right. The same two properties appear in `ranked.geojson` / `map.html` data. `rejected.csv`'s header is unchanged. No in-repo reader uses column positions. An operator spreadsheet keyed on column letters would need to shift. | `test_columns_and_brief` |
| F8-9 | MED | **Fixed.** The value is derived from the answer already fetched and cached, so a cached pre-change answer gives the same estimate. The request count is unchanged: 1 per parcel, 0 for skipped parcels or `--no-slope`. The existing cache-key pin (`test_normal_parcel_request_and_cache_key_unchanged`) is unchanged and green. Stdlib `math` only; `requirements.txt` unchanged; no settings key added. | `test_sized_answer_gives_the_estimate_off_the_parcel`, `test_spy_slope_on` (2 requests for 2 parcels), `test_skipped_parcel_is_empty_not_zero` |
| F8-10 | MED | **Verified.** The `python -m screener.autoresearch.evaluate` line is byte-identical before and after (`pool 2229, positives 34, k 45, hits 2, recall 0.059, positives_screened_out 1, score_range [6.5, 85.0], nonfinite 0`). `rank_import_problems() == []`. `git diff --stat 4fe8080` names only `checks.py`, `run.py`, `report.py`, `README.md`, the new test file and this folder. | reviewer check |
| F8-11 | LOW | **Fixed.** A MultiPolygon with a hole plus a second part goes through the same `slope_area_m2` / `_esri_polygon` selection. The 3 rings are sent, `area × MAD / 2` holds, and the floats are finite. | `test_multipolygon_with_a_hole` |
| F8-12 | LOW | **Fixed.** Workers write nothing shared. Estimates come back through `pool.map` in input order, which holds with 4 workers and 8 parcels sharing one parcel id. | `test_estimates_follow_input_order_with_threads` |
| F8-13 | LOW | **Followed.** Live evidence below is counts and rounded aggregates only. | — |

## Deviations from the review's wording (for the reviewer)

1. **Helper signature.** `_hist_balanced_earthwork(hist, area_m2, pixel_m2)` takes a third argument, the nominal pixel area at the shape's latitude, which the F8-4 coverage check needs. `slope_stats` computes it with `_nominal_pixel_m2(geom)` from the bounds' mid-latitude. Without `pixel_m2` the helper returns None. It never skips the check.
2. **G1 / G4 fixture.** G1 asks for the column to be filled "for the `GOOD_SLOPE` parcels". Under the F8-4 coverage check, `GOOD_SLOPE` on the harness's 20-acre squares is a coverage gap and correctly yields None (G3 keeps its `steep_pct` 3.3). So the filled-column assertions use `EARTH_SLOPE`, the same histogram shape scaled to the area. The G4 hand value is pinned on `GOOD_SLOPE`'s own band 1 with an area of exactly 60 pixels.
3. **Rounding rule.** The rule is 2 significant figures at every size (G9 allows that or the nearest 1,000 cy). The live median is about 110,000 cy, where the two rules agree.
4. **Return types.** `slope_stats` now returns a pair and `slope_checks` a list. Each has exactly one caller in the repo (`slope_checks` and `run.main`).
5. **Guard (b) scope.** The static test also refuses `Name` ids and `Attribute` names, not only string constants. Its docstring states that a string built at runtime gets past it, and that the spy tests are what hold.

## Guard limit (SR-16 analog)

The spy tests live in `screener/tests`, so the loop's own `evaluate.py` run doesn't execute them. They hold because the loop edits `rank.py` alone and can't reorder `run.py`. Every `exp:` diff gets a human read, and the README says so.

## Gates

| Gate | Status |
|---|---|
| G1 | Met: spies on 4 functions, slope on, `--no-slope`, failed check. Column filled with `EARTH_SLOPE` (deviation 2). |
| G2 | Met: the static test plus a self-test showing it catches a named read. |
| G3 | Met: `_slope_from` / `_slope_problem` unchanged. Band-1 faults leave the slope fields equal to `_slope_from`'s on the same answer. `steep_pct` 3.3 on `GOOD_SLOPE`. |
| G4 | Met: hand value, cut = fill, linear in area, flat = 0, units. |
| G5 | Met: skipped, failed, `--no-slope` (and `slope = false`), helper None: empty cell plus "not computed". |
| G6 | Met: one request per parcel, `requirements.txt` and settings unchanged. |
| G7 | Met: evaluate line identical, `rank_import_problems() == []`, no eval file / `rank.py` / `scoring.py` in the diff. |
| G8 | Met: root `python -m unittest discover -s tests` **1744 OK** (base 1744). Screener `python -m unittest discover -s screener/tests -t .` **162 OK** (base 134, +28). |
| G9 | Met: rounding, caveat, no dollar/flag/why entry, columns after `relief_ft`, README. The operator answers and the schema change are recorded above. |

## Live evidence (counts only)

There was one `python -m screener.run --limit 30` against the public Fulton layers and USGS 3DEP. It used the screener's own fetchers and cache, and wrote its output to a scratch directory outside the repo.

- Exit 0. 15 parcels were slope-checked, 0 slope checks failed, and 7 parcels were ranked.
- An estimate was given for 7 of 7 ranked parcels: 0 empty, and 0 empty for a coverage mismatch. So on real 3DEP answers the [0.5, 2] coverage band did not refuse any parcel in this sample.
- Rounded aggregates over the 7: earthwork 17,000 / 110,000 / 190,000 cy (min / median / max), and mean cut/fill 5.6 / 14.2 / 19.8 ft. These are single-pad figures on rolling ground. They show why the caveat says a single pad overstates large or ridged parcels.
- All 7 briefs carry the Earthwork line.
- The evaluate line was identical after the live run.

Standing risks:
- SR-6: every test runs on fakes.
- SR-8 analog: counts only. The figures above include no owner, address or parcel id.
- SR-10: no new dependency.
- SR-11: no new request.
- SR-15 / SR-16 analogs: see F8-10 and the guard limit above.
