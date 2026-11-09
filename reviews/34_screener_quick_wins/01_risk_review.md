# 01 — Screener quick wins: pre-change risk review (Agent 1)

> Written by an independent reviewer BEFORE the code. I wrote no code and
> changed no repo file except this document. Probes ran read-only from the
> worktree with `PYTHONDONTWRITEBYTECODE=1`. Base `4fe8080` (PR 38 merge).
> One section per sub-item; later sub-items append their own section here.

## Operator answers this round (2026-11-09, in chat, binding)

Recorded as given. They decide scope for this folder and for the root rounds that run alongside it.

1. **D3 (D3-01 / D3-02).** COUNT unlisted districts AND districts with unreadable setback values too. That reverses the reviews/29 dispositions D3-01 and D3-02, which kept both as BTS N/A. The change belongs to the root D3 round, not to this folder.
2. **D2 flood scope.** "Just review". D2 stays as implemented: V/VE are review-only and the floodway still blocks. No flood-rule change.
3. **Null-beats-low** (reviews/20_next_improvements/00_overview.md). **Not answered.** No new root sub-score may land until it is answered.
4. **"Fix".** Fix the research.py ArcGIS error-envelope bug and the audit-path bug, and do the D1-11 `zoning_import` fetcher round. All three are root work and not in this folder.
5. **"Can you add this yourself".** The operator authorizes the agent, acting for the human between runs, to apply the reviews/26 hazards prepare-mutation to `prepare.py`. That is root work under the prepare-mutation protocol. It needs a new run + baseline (SR-15) and is not in this folder.
6. **Step 2: build the screener quick wins.** Human-side edits to `screener/` are authorized, EXCEPT the eval files:
   - `screens.py`
   - `autoresearch/evaluate.py`
   - `autoresearch/build_eval.py`
   - `autoresearch/data/`
   - `settings.toml` (defaults may only gain new keys)

   So there is no new eval baseline. The cross-cutting rule from the background analysis applies to every sub-item: a live-only field is attached AFTER scoring (or kept where `rank.py` cannot reach it). It is guarded so that `rank.py` cannot read data the eval never measures.

Tier: these are screener human edits. They are not under the root five-file contract, so CLAUDE.md's scope note applies. I review each one at the Tier 1 level, with one independent reviewer after the code. F8 touches the live slope path (`checks.slope_stats`/`slope_checks`), which feeds the steep-ground screen, so the review must look at that path closely. No Tier 2 surface is involved: no evaluator, no metric, no credentials, no new external service.

---

## F8 — Cut/fill earthwork from the 3DEP elevation histogram

**Requirement:**
- A pure-Python, balanced cut/fill estimate from the band-1 elevation histogram that `checks.slope_stats` already fetches.
- The area is computed in `slope_stats`, and the helper never raises.
- The result is attached as a brief fact and a `ranked.csv` column AFTER scoring. `rank.py` must not read it, guarded with the rank_problems pattern.
- No new dependencies.

### Verified facts this review relies on

| # | Fact | Evidence |
|---|---|---|
| V1 | In a live run `scoring.score(p)` passes the WHOLE parcel dict to `rank.learned_score(p)`. Any key on `p` at that moment is readable by the loop's file. | `screener/scoring.py` `score()`; `screener/run.py` survivor loop (`p.update(scoring.score(p))`) |
| V2 | **A private key is not a guard.** `evaluate.rank_problems` refuses private *attributes* (`.x` starting with `_`), not string subscripts. Today's `rank.py` plus `p.get("_earthwork")`, `p.get("earth" + "work_cy")` and `p["_open_ground"]` returns `[]`, and `rank_import_problems()` is `[]` at base. | probe: `rank_problems(rank.py + snippet)` gave `[]` |
| V3 | The eval set has no elevation or slope. `build_eval.BANNED_AS_NONE` sets `steep_pct`, `relief_ft` and `mean_slope_pct` to None, `usable_after_slope = usable_acres` there, and the 2016 key cannot use 2018-19 lidar (graded pads read flat). A new key absent from eval records reads as None in the eval and as a value live: a live/eval split the loop could learn from if it reached `p` before scoring. | `autoresearch/build_eval.py` (FEATURES, BANNED_AS_NONE, "no slope in the eval set", docstring on slope) |
| V4 | `slope_stats(geom)` sends `_esri_polygon(geom)`: top-level Polygon parts, each simplified at 1e-5 degrees, with holes. `slope_area_m2(geom)` measures the same top-level parts, unsimplified, through `LocalProjection`. It returns 0.0, and does not raise, for empty, line-only or zero-area shapes. Both are called on `p.get("_open_ground") or p["geometry"]`. | `checks.py` `_esri_polygon`, `slope_area_m2`, `slope_checks` |
| V5 | `_slope_problem` runs `_slope_from` as the `request_json` check. Anything raised inside `_slope_from` makes a usable answer a FAILED slope check (retried, not cached) and changes the steep-ground screen. | `checks.py` `_slope_problem`, `arcgis.request_json(check=...)` |
| V6 | Band 1 is raw 3DEP elevation in metres (`"$$"` in the CompositeBand rule, F32). `hists[0]` carries `min`, `max` and `counts`, and bin width is `(max-min)/len(counts)`, as `_hist_quantile` already assumes. | `checks.py` `SLOPE_RULE`, `_hist_quantile` |
| V7 | The README records that across 2026-10-28/29 the 3DEP **elevation** statistics were identical for repeated requests while slope statistics differed on about 22% of them. An elevation-only volume is therefore run-to-run stable where `steep_pct` is not. | `screener/README.md` "Public data changes between days" |
| V8 | Fixture `GOOD_SLOPE` band 1: min 250, max 262, counts [10, 20, 20, 10]. Bin centres are 251.5 / 254.5 / 257.5 / 260.5, so the histogram mean is 256.0 m, which equals `statistics[0].mean`. The mean absolute deviation (MAD) from it is 2.5 m. Balanced cut = fill = area × MAD / 2 = 1.25 m³ per m² of open ground. | hand calculation from `tests/test_integrity.py` `GOOD_SLOPE` |
| V9 | `ranked.csv` / `ranked.geojson` / `map.html` all take `RANKED_COLUMNS`. Every in-repo reader uses `csv.DictReader` (tests), so nothing reads columns by position. | `report.py` `write_all`; grep of `ranked.csv` / `RANKED_COLUMNS` |

### HIGH

**F8-1. The live-only leak into `rank.py` (V1, V2, V3).** The natural code puts `earthwork_cy` and `mean_cut_fill_ft` in `slope_stats`'s return dict. `slope_checks` would then `p.update(...)` them, so they would sit on `p` when `scoring.score(p)` runs, and the loop could read them. A leading underscore does not help (V2). Neither does adding the key to `BANNED_AS_NONE`: that is an eval file, and the entry would have no effect until a rebuild.

*Mitigation (structural; this is the real guard):*
- The earthwork values must be **absent from `p` while `screens.site_rejects`, `screens.flags` and `scoring.score` run**.
- `slope_checks` keeps the slope keys exactly as today and returns earthwork through a side channel:
  - Either return a list aligned with the input order, or return pairs from `work()`.
  - Collect them in the main thread, not by writes from worker threads into shared state.
  - Avoid a dict keyed by `parcel_id` unless uniqueness is asserted, since nothing upstream guarantees unique ids.
- `run.main` attaches the values to the survivors only after the `scoring.score` / `screens.flags` loop and the final sort. That is before `report.write_all`, which is the only reader.
- Do not change `scoring.py` or `rank.py`. Feeding earthwork into the fixed `topography` part is a later human decision, outside this item.

*Guard (the "rank_problems pattern"; evaluate.py is an eval file and stays unchanged):*
- (a) **Behavioural spy test, the decisive one.** An offline end-to-end `run.main` on the existing fixtures, with `GOOD_SLOPE` for the slope route. `rank.learned_score` and `scoring.fixed_score` are wrapped to record `set(p)` on every call. The test asserts that no recorded key set contains an earthwork key, and that `ranked.csv` then carries the populated column for the same parcels. Run it with slope on and with `--no-slope`.
- (b) **Static defence in depth**, mirroring `rank_problems`. Parse `screener/rank.py` and `screener/scoring.py` with `ast` and refuse any string constant containing `earthwork` or `cut_fill`. State in the test docstring that a computed string gets past it, and that (a) is what holds.

This guard lives in `screener/tests`, so the loop's own `evaluate.py` run does not execute it. That is acceptable only because the loop edits `rank.py` alone and cannot reorder `run.py`. Say so in the README / decision note (SR-16 analog: every `exp:` diff gets a human read).

**F8-2. A helper exception would turn good slope answers into failed checks (V5).** If the earthwork math sits inside `_slope_from`, any malformed band-1 detail would raise inside `_slope_problem`: a mismatched size, `max < min`, a negative or None count, NaN, an overflow, a zero area. A usable answer would then be retried and recorded as `slope_error`, and the steep-ground screen would stop being applied to that parcel.

*Mitigation:*
- Leave `_slope_from` and `_slope_problem` byte-for-byte unchanged.
- In `slope_stats`, after `_slope_from(data)` succeeds, compute `area = slope_area_m2(geom)` and call a TOTAL helper `_hist_balanced_earthwork(hists[0], area)` that returns `None` (never raises) when:
  - the histogram or its counts are missing or unreadable;
  - the total count is not positive;
  - the area is not positive;
  - any count is negative, None or non-finite;
  - any intermediate value is non-finite.
- Catch the same exception tuple `_slope_problem` uses, plus `ZeroDivisionError` and `OverflowError`. Check `math.isfinite` on every output.
- Earthwork never decides whether a slope answer is usable.

**F8-3. Over-claiming what the number means.** One balanced flat pad over the whole open ground is a rough grading-difficulty signal, not a grading plan. It ignores:
- the drainage cross-slope (industrial pads are built at about 0.5–2%, not dead flat);
- leveling only the building or yard footprint rather than all open ground;
- terracing on large or ridged parcels, where a single pad grossly overstates the volume;
- shrink/swell and compaction factors, topsoil stripping, rock and unsuitable soils;
- ground already graded since the 2018-19 lidar.

A raw `123,456 cy` reads as an engineering quantity.

*Mitigation:*
- Round the volume (to 2 significant figures, or to the nearest 1,000 cy above 10,000).
- Round `mean_cut_fill_ft` to 0.1 ft.
- Label the brief line, in substance: "rough balanced cut/fill to level the open ground to one flat pad at its mean elevation (USGS lidar 2018-19, 10 m grid); not a grading plan: ignores drainage slope, terracing, shrink/swell, topsoil and rock".
- No dollar figure, no flag, and no entry in `why_site` (that list is scoring output).
- Document the two columns in `screener/README.md`, with the same caveat and the V7 stability note.

### MEDIUM

**F8-4. Area basis and NoData (V4).** Per-pixel area can come from `area / Σcounts`, which is ground-true and has no Web Mercator correction, or from the nominal 100 × cos²(lat) m² (about 69 m² at 33.7°N). The two disagree by perimeter effects on small or thin shapes, by the 1e-5° simplification, and by any NoData gap. The first form spreads the covered pixels' depth distribution over the whole area, which is an extrapolation.

*Mitigation:*
- Use `area / Σcounts`, as the background analysis recommends. The volume is then `area × MAD / 2`, so it does not depend on the pixel-size estimate, and only the representativeness of the counted pixels matters.
- Add one sanity cross-check: if `Σcounts × nominal_pixel_m2` lies outside [0.5, 2] × area, return None. This catches a coverage gap or a geometry/answer mismatch.
- Pin it with a fixture whose counts are far too few for the area.
- Parcels that are too small or skipped (`too_small_for_slope`) already skip the fetch and get None.

**F8-5. Bin quantization.** Bin centres are uniform within each bin. Per-pixel depth error is at most half the bin width, so the MAD error is at most `(max-min)/(2·len(counts))`. That is negligible for a real 3DEP histogram and large for the 4-bin fixture.

*Mitigation:* use bin centres consistently (the same convention as `_hist_quantile`'s width). Take the balance elevation as the **histogram** mean, not `statistics[0].mean`, so cut = fill exactly by construction. Put the bound in the docstring.

**F8-6. Units.**
- Band 1 is in metres (V6).
- `cy = m³ × 1.307950619...` (exact: 1 yd³ = 0.764554857984 m³).
- `ft = m × FT_PER_M`, reusing the `geo.FT_PER_M` constant rather than a new literal.

*Mitigation:* a test pins the conversion against the exact definition and checks the V8 fixture value by hand. That gives a cut of 1.25 m³/m² × area and `mean_cut_fill_ft = MAD × FT_PER_M = 8.2 ft`, where (cut+fill)/area = MAD.

**F8-7. Null paths must be explicit.** These paths must each give `earthwork_cy` / `mean_cut_fill_ft` None, and they must give empty cells in `ranked.csv` (`_cell(None) == ""`), not missing columns or 0:
- `SLOPE_SKIPPED`;
- `slope_error`;
- `--no-slope`, or `run.slope = false`;
- a total-helper None (F8-2, F8-4).

0 would read as "flat". The brief prints "not computed" plus the reason that already applies (slope not checked or failed).

*Mitigation:* one test per path.

**F8-8. Output-schema change (V9).** Two new `RANKED_COLUMNS` entries change the CSV header and the geojson/map properties. Nothing in the repo reads by position. An operator spreadsheet keyed on column letters would shift.

*Mitigation:*
- Insert both columns directly after `relief_ft`, which is the readable place.
- Name the change in the decision note and in the README `ranked.csv` row.
- Keep both columns out of `rejected.csv`, whose fixed header stays unchanged.

**F8-9. No extra network, no new dependencies (SR-10, SR-11, SR-6).** The value derives from the answer already fetched and cached. A cached answer from before the change yields the same volume.

*Mitigation:*
- In the offline run, `session.count(SLOPE_URL)` stays 1 per parcel (the existing `test_good_answer` shape).
- Use stdlib `math` only, and leave `requirements.txt` unchanged.
- No settings key is needed. If one is added anyway, it must be a NEW key with the feature on by default, and no existing default may change.

**F8-10. Eval untouched (SR-15 analog).** There is no baseline, so the evaluator output must be identical before and after.

*Mitigation:*
- `python -m screener.autoresearch.evaluate` gives the same `pool / positives / k / hits / score_range / nonfinite` line before and after.
- `rank_import_problems() == []`.
- `git diff --stat` touches none of: `screens.py`, `autoresearch/evaluate.py`, `autoresearch/build_eval.py`, `autoresearch/data/`, `settings.toml`, `rank.py`, `scoring.py`.

### LOW

**F8-11. Geometry shapes.**
- MultiPolygon open ground, holes, a GeometryCollection with lines, and a huge parcel (`max_acres`) all go through the same `slope_area_m2` and `_esri_polygon` part selection.
- Floats stay finite.

*Mitigation:* one MultiPolygon-with-hole fixture asserting that area × MAD / 2 holds.

**F8-12. Threading.** `slope_checks` runs `work()` in a thread pool.

*Mitigation:* return the earthwork value from `work()` and collect it from `pool.map` in the caller (F8-1). No shared mutable state is written from workers.

**F8-13. Live evidence must be counts only (SR-8 analog).** One `--limit 30` live run is allowed if the network permits.

*Mitigation:*
- Record only counts:
  - parcels with a value;
  - parcels with None, by reason;
  - the identical-evaluate check.
- Optionally give the min/median/max volume, rounded.
- No owner names, addresses or parcel ids in the review docs.

### Go / no-go gates (all must hold before the decision note says go)

| Gate | Condition |
|---|---|
| G1 | Spy test: in an offline `run.main` with slope on and with `--no-slope`, no key set seen by `rank.learned_score` or `scoring.fixed_score` contains an earthwork key. `ranked.csv` has the column populated for the `GOOD_SLOPE` parcels (F8-1a). |
| G2 | Static test: no string constant in `rank.py` or `scoring.py` contains `earthwork` or `cut_fill`. Its docstring states the computed-string limit (F8-1b). |
| G3 | `_slope_from` and `_slope_problem` are unchanged. Every malformed fixture in `test_unreadable_histograms_do_not_crash`, plus band-1-only faults (size mismatch, `max < min`, a negative or None count, NaN, area 0, a count/area mismatch), leaves the slope fields identical to base and gives earthwork None, never an exception. `steep_pct` is still 3.3 on `GOOD_SLOPE` (F8-2, F8-4). |
| G4 | Maths: the V8 hand value, cut == fill, linear in area, 0 for a single-bin or flat histogram, the cy and ft constants (F8-5, F8-6). |
| G5 | One null-path test each: skipped, failed, no-slope, helper None. Each gives an empty CSV cell and the brief's "not computed" text (F8-7). |
| G6 | No extra HTTP call: the slope request count is unchanged. `requirements.txt` is unchanged (F8-9). |
| G7 | Eval identical: the evaluate output line matches base, and the diff touches no eval file, `rank.py` or `scoring.py` (F8-10). |
| G8 | Both suites green, and the decision note records the counts: root `python -m unittest discover -s tests` (base about 1744 OK) and `python -m unittest discover -s screener/tests -t .` (base 134 OK). |
| G9 | README: the column meanings and the F8-3 caveat. The decision note records the operator answers above and the schema change (F8-8). |

**No-go triggers:**
- An earthwork key is on `p` at scoring time.
- Any change to an eval file, `rank.py` or `scoring.py`.
- A slope-field difference on any existing fixture.
- A new dependency or an extra request.
- Earthwork wired into a score, a screen or a flag.

**Verdict: GO with conditions G1–G9.**

Standing risks:
- SR-6: offline fixtures only.
- SR-8 analog: counts-only evidence.
- SR-10: no new dependencies.
- SR-11: no new requests.
- SR-15 / SR-16 analogs: the eval boundary, and the after-scoring attachment as the structural guard, with a human read of `exp:` diffs as the backstop.
