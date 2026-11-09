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

---

## F14 — Decision capture, Phase A (pursue / pass / maybe + per-run feature snapshot)

> Same independent reviewer, appended BEFORE the code, after F8 landed
> (`d5de143`). No code written. Probes were read-only: `git check-ignore`,
> greps, and line counts. Counts only, no parcel ids. The operator answers
> recorded at the top of this document apply unchanged. For this item that
> means human-side screener edits are authorized, the eval files are not, and
> there is no new eval baseline.

**Requirement (as given):**
- `screener/inputs/decisions.csv` gains `maybe` alongside `pursue` / `pass` / `listed` (`run.load_decisions`).
- A new stdlib CLI `screener/decide.py` records pursue/pass/maybe plus a reason per parcel id against the latest output dir.
- Each run writes a feature snapshot covering ALL eval `FEATURES`, not only the 12 ranking columns that overlap them in `report.RANKED_COLUMNS`, so decisions can later become eval labels.
- `evaluate.fingerprint()` and `EVAL_FILES` stay unchanged.
- Phase B (labels into the eval) is documented here as a human `--new-baseline` step and NOT done.
- Files that hold user decisions must never trip the evaluator's refusals.

Tier: **Tier 1**. The change adds no evaluator, metric, credential or network surface, and `decide.py` is offline only. The leak and refusal surfaces below are why one independent post-change reviewer must check the gates and not the code writer alone.

### Verified facts this section relies on

| # | Fact | Evidence |
|---|---|---|
| W1 | `load_decisions` keeps a row only if `decision in {"pursue","pass","listed"}`. A `maybe` row today is **silently dropped**, so it reads as "no decision". Keys are `parcel_id.strip()`, and internal whitespace is kept (ids like `14F0063  LL0219` have two spaces). The last row for an id wins (dict overwrite). If the file is absent it is created with header `parcel_id,decision,date,note`. | `screener/run.py` `load_decisions`, `DECISIONS_HEADER`; `screener/inputs/README.md` |
| W2 | Decisions act on the funnel only through `screens.attribute_rejects` (an EVAL file). It reacts to `pass` (rejected: "you passed on this") and to `listed` (with `exclude_listed`), and to nothing else. A `maybe` mark therefore passes `screens.py` untouched, and `screens.py` needs no edit. | `screener/screens.py` lines 12-19 |
| W3 | `p["decision"]` is set in `run.main` AFTER `p.update(scoring.score(p))` and `screens.flags(p)`. Today a decision is never on `p` while `rank.learned_score(p)` runs (F8 V1: scoring passes the whole dict). `rank.learned_score` receives the live dict itself, not a copy, so it can mutate `p`. | `screener/run.py` survivor loop; `screener/scoring.py` `score()` |
| W4 | `evaluate --decide` refuses (exit 3) on any line from `git status --porcelain --branch --untracked-files=all -- screener`. Ignored files do not appear there; untracked non-ignored files do. | `evaluate.py` `STATUS_ARGS` and `decide()`'s `git_status[1:]` refusal |
| W5 | `screener/.gitignore` ignores `cache/`, `output/`, `autoresearch/experiments.tsv`, `inputs/decisions.csv` and `autoresearch/best_ranking.json`, and only these exact paths. `git check-ignore` confirms that `screener/output/x/features.jsonl` IS ignored, and that `screener/inputs/decisions.csv.tmp` and `screener/inputs/decision_features.jsonl` are **NOT** ignored. Any temp file, backup or sidecar next to `decisions.csv` would make every later `--decide` refuse until it is removed. | `git check-ignore -v` probe (rc 0, one match of three) |
| W6 | `EVAL_FILES = (evaluate.py, autoresearch/data/, screens.py)`. `fingerprint()` hashes the two data files only. `run.py`, `report.py`, `decide.py` and the READMEs are outside both, so Phase A needs no baseline. Note one wrinkle: `--decide` refuses when **HEAD** touches anything but `rank.py`, so the human commit that lands F14 cannot itself be scored. The next `exp:` commit on top of it is unaffected. | `evaluate.py` lines 76-77, 118-123, 455-458 |
| W7 | `build_eval.FEATURES` has 25 names. Every one is produced on the live path: in `fulton.py` (assessor/CAMA), in `checks.py` (map checks), or in `run.py` (`usable_after_slope`). `BANNED_AS_NONE` (13 names) is set to None in every eval record. `RANKED_COLUMNS` carries 12 of the 25. Missing from it, among others, are `usable_acres`, `floodway_pct`, `water_pct`, `easement_pct`, `protected_pct`, `constrained_pct`, `rail_owner`, `class_code`, `land_use_code`, `owner_po_box`, `sales_count`, `inherited` and `distress_owned`. | `autoresearch/build_eval.py` lines 79-89; grep of key literals |
| W8 | Several eval features differ in meaning between the eval and live builds, even with the same name: <br>• `usable_after_slope` = `usable_acres` in the eval but slope-adjusted live. <br>• `rail_ft` / `rail_owner` come from main lines and yards only (`NET IN ('M','Y')`) in the eval but from the full live rail layer. <br>• Ownership fields are as of 2016-01-01 in the eval and as of today live. | `build_eval.py` `eval_layers["rail"]`, the "no slope in the eval set" line, `AS_OF` |
| W9 | Eval records carry `key`, `block` and `split`, with `block` = hash of the ~5 km cell from `cell_for(lon, lat)`. The clustered bootstrap needs the cell, so a future label set needs each parcel's lon/lat (or cell). | `build_eval.py` `cell_for`, record build |
| W10 | The output dir is `output/<county>-<YYYY-MM-DD>` unless `--out` is given. A second run on the same day **overwrites** it. A `pass` mark drops the parcel at `attribute_rejects` in every later run, so after a pass its features are never computed again. | `run.py` `out_dir`; W2 |
| W11 | The offline test base already patches `run.DECISIONS_FILE` into a temp dir. The worktree's real `inputs/decisions.csv` holds a header only, 1 line, and `output/` is empty. | `tests/test_integrity.py` `Offline.setUp`; `wc -l` / `ls` counts |
| W12 | `PROGRAM.md` tells the loop that decisions "are not in the score yet. Add them as a second term once there are about 30." | `autoresearch/PROGRAM.md` line ~161 |

### HIGH

**F14-1. Decisions are labels, so they must never reach `rank.py` (W3; F8 V1/V2 analog).** Once Phase B exists, a decision on `p` at scoring time would be target leakage in the live tool: the loop's file would see the answer. The same holds for any snapshot handle or id that points at one. Today the ordering protects this, and Phase A must keep it that way. There are tempting shortcuts that break it:
- attaching `decision` early, for example so `maybe` can sort higher;
- passing `decisions` into `scoring` or `checks`;
- having the snapshot writer stash a `_snapshot` / `_decision` key on `p` before scoring (a private key is not a guard, F8 V2).

*Mitigation:*
- `decision` (any value, including `maybe`) and any snapshot or run identifier stay OFF `p` until after `scoring.score`, `screens.flags` and the sort, exactly as `decision` is placed today.
- `maybe` gets no ranking, score, flag or `why_*` effect. It is a mark in `ranked.csv` / `rejected.csv` / the brief and nothing more.
- No change to `scoring.py`, `rank.py` or `screens.py`.

*Guard:* extend the F8 spy test (G1 there) so that, across one offline `run.main` with a `decisions.csv` holding one each of pursue / maybe / listed / pass, no key set seen by `rank.learned_score` or `scoring.fixed_score` contains `decision` or any snapshot key. The same run must show `maybe` in `ranked.csv`'s `decision` column and the `pass` parcel in `rejected.csv`.

**F14-2. A stray file under `screener/` locks the loop (W4, W5).** The natural implementations each write a file the evaluator would refuse on:
- an atomic rewrite of `decisions.csv` through `decisions.csv.tmp` (SR-7's `os.replace` pattern);
- a backup such as `decisions.csv.bak`;
- a sidecar store such as `inputs/decision_features.jsonl`;
- a snapshot written anywhere outside `output/`.

None of these is ignored today. A crash mid-write would leave the temp file behind, and every `evaluate --decide` would then refuse with "untracked files under screener/". That fails closed (no corrupt score), but the run is blocked, and an operator might "fix" it with `git add`, which commits personal notes and owner details.

*Mitigation:*
- Every new runtime file goes under an ignored path, and `screener/.gitignore` is extended in the same commit. Examples: `inputs/*` except the tracked `inputs/README.md` (`inputs/*` + `!inputs/README.md`), or explicit patterns for each new name, including the temp name.
- Temp files are created in the same directory under an ignored name, so `os.replace` stays atomic on one filesystem.

*Guard:*
- A test runs `git check-ignore` for every path `decide.py` and the snapshot writer can create, including the temp name, using the code's own path constants and not hand-copied strings. It skips if git is unavailable, and says so.
- A test asserts that `inputs/README.md` is still tracked and not ignored.

**F14-3. A label's features can be lost or mismatched (W10, W8).** The requirement is that decisions "can later become eval labels". That needs the features as they were when the person decided. Three ordinary events break the per-run snapshot as the only copy:
- (a) a same-day re-run overwrites `output/<county>-<date>/`;
- (b) the operator deletes `output/`, which is a cache-like, gitignored folder;
- (c) a `pass` removes the parcel from every later run (W10), so no later snapshot has it.

A decision then has no features, or gets joined to features from a different run, with different layer vintages, a trial `--limit` run, or a `--no-slope` run.

*Mitigation, one of these two (the code writer's choice):*
- (i) At decision time, `decide.py` copies that parcel's snapshot row, together with the snapshot's run metadata, into an append-only, gitignored decision-side store under `inputs/`.
- (ii) Each snapshot gets a unique run id, a timestamped file name and a `--keep` policy, and `decide.py` records that run id in the decision.

(i) survives all three events; (ii) survives only (a). **Prefer (i).**

*Either way:*
- Snapshot metadata names: run date and time, county, `--limit` (a trial run), slope on/off, the settings file's SHA-256, and the code commit if git answers (else `null`, never a crash).
- `decide.py` refuses to record a decision on a parcel absent from the chosen run's snapshot unless `--no-features` is given. In that case the decision is still written and the missing features are named.

*Guard:* tests for (a), (b) and (c). After a re-run, after deleting the output dir, and on the run after a `pass`, the stored features for that decision are unchanged.

### MEDIUM

**F14-4. The snapshot must be what the eval would have seen (W3, W7, W8, W9).**
- Take it from an **explicit allow-list**: `build_eval.FEATURES`, imported rather than copied, so the list cannot drift. Never take it from `p.items()`. That keeps out:
  - the geometry;
  - F8's `earthwork_*`;
  - owner name and mailing (SR-8 analog: the output dir already holds them in `ranked.csv`, but the label store should not);
  - `decision` itself.
- Add `parcel_id`, `lon`, `lat` (W9), plus the live values of the `BANNED_AS_NONE` fields in a separate, clearly named sub-object. Phase B can then choose to null them as the eval does, and nothing is lost.
- Take it **before `scoring.score(p)`**, as a copy of plain scalars. `rank.learned_score` gets the live dict and could mutate it (W3); the eval hands rank a fresh copy per record.
- Write it for the ranked survivors, the eval-pool analog, and say so in the README. A rejected parcel's map features are partial.
- `import build_eval` pulls in shapely and the screener modules, which are already dependencies, and runs no I/O at import. Gate: importing it does nothing on the network (offline-test patched session).

*Guard:*
- The snapshot's feature keys equal `build_eval.FEATURES` exactly.
- No earthwork, owner-name, geometry or decision key is present.
- A fixture where a spy `rank.learned_score` mutates `p["acres"]` still snapshots the pre-score value.

**F14-5. Value encoding.**
- CSV loses None vs `""`, and bools come out as "yes"/"" through `report._cell`. Use JSON, mirroring the eval's `{"meta":…, "records":[…]}` shape, gz optional.
- Use `json.dumps(..., allow_nan=False)`. A NaN or inf feature becomes `null`, and the summary note counts how many. Never write the non-standard `NaN` token, which Python's own reader accepts but other readers do not.
- Write atomically (temp name in the same ignored directory, then `os.replace`, SR-7).

*Guard:* a NaN fixture gives `null` and a counted note, and the file round-trips through `json.loads`.

**F14-6. Writing to the human's own file safely.** `decisions.csv` is hand-edited, often in a spreadsheet. `decide.py` must not corrupt it:
- **Last line without a trailing newline:** a plain append would glue the new row into the previous row's `note`. Check the last byte and add `\n` first.
- **UTF-8 BOM from Excel** (`﻿parcel_id` header): `DictReader` then finds no `parcel_id` column, and every decision silently vanishes. That is already a latent bug in `load_decisions`. Read with `encoding="utf-8-sig"` in both `run.py` and `decide.py`.
- **CRLF:** let the `csv` module handle it (`newline=""`).
- **Commas, quotes or newlines in the reason:** use `csv.writer`, never string joins.
- **Header:** keep the four-column header unchanged. "reason" maps to `note`. If a column is added anyway (e.g. `run`), old four-column files must keep loading, and new rows must not shift `note`. Test both shapes.
- **Missing file:** `decide.py` creates it with `run.DECISIONS_HEADER`, never a second copy of the header.

*Guard:* one test per bullet, including a BOM file whose decisions still drop a `pass` parcel in `run.main`.

**F14-7. Picking the wrong parcel or the wrong run.**
- **Ids:** match ids exactly after `.strip()` (W1), never collapsing internal whitespace. Look the id up in the chosen run's `ranked.csv` / snapshot (and `rejected.csv` for a pass on a rejected parcel). An unknown id refuses (exit 2) and prints the nearest exact-length candidates, never a guess.
- **No rank shorthand:** no bare "decide 12 pursue". Some county ids could be all digits, so a rank selector, if added at all, needs an explicit `--rank N`.
- **Latest run:** take the newest `ranked.csv` by mtime under `output/*/`, print the dir it chose, and accept `--run DIR` to override. Refuse if there is none, or if two counties' latest runs tie, rather than guess.
- **Pass:** `pass` prints its consequence before writing ("this parcel will be dropped from future runs"), since W2 makes it a screen.
- **Allowed values:** accept exactly `pursue | pass | maybe | listed`, case-insensitive. Keep `listed`, the existing vocabulary, so the CLI never rejects a value the run accepts. Anything else refuses.
- **Repeats:** a repeated identical decision appends one more row; last wins, as today (SR-9). Rows are never rewritten in place. History is the label record.

*Guard:* tests for exact-match ids with internal spaces, an unknown id, no runs, `--run`, every value plus an invalid one, and last-wins after a repeat.

**F14-8. Tests must never touch the real user file (W11, SR-6).** `decide.py` must resolve the decisions path through `run.DECISIONS_FILE` **at call time** (or take an explicit `--file`), so the existing `Offline` patch covers it. The output root needs the same treatment (`run.HERE / "output"`, patchable).

*Guard:* the test base patches both. One test fails if either real path's mtime or existence changes during the suite.

### LOW

**F14-9. No network, no dependencies (SR-10, SR-11).** `decide.py` imports only stdlib modules and `screener.run` constants. It must not import `arcgis` at module top for anything it would use. Importing `run` does pull `arcgis` in, which is acceptable because it makes no calls at import. `requirements.txt` is unchanged.

**F14-10. Docs.** Update the following:
- `inputs/README.md`: `maybe`, `decide.py` usage, and the BOM/newline notes.
- `screener/README.md`: the snapshot file and its fields, the W8 meaning caveats, and the decision-side store.

Do **not** edit `autoresearch/PROGRAM.md` in Phase A. It is the loop's instruction file, and W12 already states the Phase B threshold. Changing it mid-chain changes the loop's behaviour without a baseline.

**F14-11. Counts-only live evidence (SR-8 analog).** If the one allowed `--limit 30` live run happens, record only:
- the snapshot record count against the ranked count;
- the count of `null` features by name;
- that a `decide.py` round trip (pursue / maybe / pass on three parcels, restored afterwards) showed the expected `decision` cells and one extra rejected parcel on re-run.

No ids, owners or addresses. Restore the real `decisions.csv` byte-for-byte afterwards; it holds a header only today (W11).

### Phase B: documented here, NOT done in this item

Turning decisions into labels is a **human `--new-baseline` step**:

1. **Gather enough labels first.** About 30 decisions, per W12, and preferably over 100 before they decide keeps.
2. **The human builds a label set from the decision-side store.** It is a new data file, so `fingerprint()` changes, or `evaluate.py` gains a second term; either way an eval file changes.
3. **The human records `--new-baseline`.** That starts a new chain (W6). Earlier keeps do not carry over.

Phase B must answer the following before it lands; none is answered here:
- **Selection bias.** People only review what an earlier `rank.py` put near the top. "Not reviewed" is not "pass", and a pursue/pass term rewards agreeing with the old ranking. This needs pursue-vs-pass within reviewed parcels only, or an exploration slice.
- **Meaning drift (W8).** Live features differ from 2016 eval features with the same names, so the two terms cannot share one record schema without a rule for each differing field.
- **Clustering (W9).** Decisions cluster spatially, like the existing positives, so bootstrap by cell.
- **Privacy.** The label file holds the operator's decisions. It must stay gitignored, or contain only hashed keys and features, never notes. `evaluate`'s `RANK_BANNED_STRINGS` should then name it.
- **Leakage (F14-1).** It becomes a live correctness property, not only a hygiene rule.

### Go / no-go gates for F14 (all must hold before the decision note says go)

| Gate | Condition |
|---|---|
| H1 | Spy test: with pursue / maybe / listed / pass rows, no key set seen by `rank.learned_score` / `scoring.fixed_score` holds `decision` or a snapshot key. `maybe` shows in `ranked.csv`, `pass` lands in `rejected.csv`, and `listed` is unchanged (F14-1). |
| H2 | `maybe` is accepted by `load_decisions`, has no score, flag, sort or screen effect, and the evaluator output line is identical to base. `git diff --stat` touches no `EVAL_FILES` entry and not `build_eval.py`, `autoresearch/data/`, `rank.py`, `scoring.py`, `settings.toml` or `PROGRAM.md`. `fingerprint()` is unchanged (W6). |
| H3 | Every path the new code can write, temp names included, is ignored under `screener/`. `inputs/README.md` stays tracked. A forced crash between the temp write and `os.replace` leaves only ignored files (F14-2). |
| H4 | The snapshot's feature keys equal `build_eval.FEATURES`. It holds no earthwork, geometry, owner-name or decision key, it carries lon/lat, run metadata and `BANNED_AS_NONE` values kept apart, it is taken before scoring (mutation spy), and it writes NaN as `null`, counted (F14-4, F14-5). |
| H5 | A decision's features survive a same-day re-run, deletion of `output/`, and the next run after a `pass` (F14-3). |
| H6 | `decisions.csv` robustness: missing final newline, BOM (including `run.main` honouring a pass), CRLF, a reason with comma/quote/newline, the old four-column header, and creation when absent (F14-6). |
| H7 | CLI behaviour: exact ids with internal spaces, unknown-id refusal, no-run refusal, `--run`, every value plus an invalid one, the `pass` consequence printed, append-only with last wins (F14-7). |
| H8 | Neither the real `inputs/decisions.csv` nor the real `output/` is touched by the suite, and `decide.py` makes no network call (F14-8, F14-9). |
| H9 | Both suites green, with counts in the decision note: root about 1744 OK at base, screener 134 OK at base plus F8's additions. Docs per F14-10. The decision note restates the operator answers and that Phase B is not done. |

**No-go triggers:**
- A decision or snapshot key on `p` at scoring time.
- Any eval-file, `rank.py`, `scoring.py`, `settings.toml`-default or `PROGRAM.md` change.
- A new runtime file that `git check-ignore` does not cover.
- A snapshot built from `p.items()` rather than `FEATURES`.
- Any write to the real decisions file from a test.
- Any Phase B work (a label file read by the evaluator, or a `--new-baseline`).

**Verdict: GO with conditions H1–H9.**

Standing risks:
- SR-6: offline fixtures, patched paths.
- SR-7: atomic writes under ignored names.
- SR-8 analog: no owner names in the label store; counts-only evidence.
- SR-9: append-only, last wins.
- SR-10: stdlib only.
- SR-11: no requests.
- SR-15 / SR-16 analogs: the eval boundary is unchanged. Labels stay off `p` until after scoring, the human read of `exp:` diffs is the backstop, and Phase B is the human baseline step.

---

## F2 — Owner clustering, phase 1 (portfolio and adjacent same-owner holdings)

> Same independent reviewer, appended BEFORE the code, after F14 landed
> (`698e206`). No code written. Probes were read-only: greps, line reads and
> the fixture builder. Counts only, no parcel ids or owner names. The
> operator answers recorded at the top of this document apply unchanged:
> D3 counts unlisted districts and unreadable setbacks (root D3 round); D2
> flood scope "just review", no flood-rule change; null-beats-low NOT
> answered, so no new root sub-score; "fix" the research.py ArcGIS
> error-envelope and audit-path bugs and do the D1-11 `zoning_import`
> fetcher round (root); "can you add this yourself" authorizes the agent,
> acting for the human between runs, to apply the reviews/26 hazards
> prepare-mutation (root, new run + baseline); step 2 builds these screener
> quick wins as human-side edits, eval files excluded, no new eval
> baseline.

**Requirement (as given):**
- New `screener/owner_clusters.py`, stdlib + shapely only.
- Cluster owners on the FULL parcel list, before the attribute rejects (`run.py` lines 97-118 today; the requirement's "~99-107").
- Match on normalised owner name AND normalised mailing address. Do not reuse `owners.same_owner`'s name-only rules.
- Detect adjacency (touching, or within a few feet) to surface assemblages, meaning one owner/cluster holding adjacent parcels that sum into 5-50 ac, and report portfolio size.
- Attach as flags / brief lines next to the slope-skipped flag (`run.py` lines 231-233 today; the requirement's "~206-211"), not in `screens.flags` (`screens.py` is an EVAL file).
- Not a rank feature.

Tier: **Tier 1**, screener human edit. The change adds no evaluator, metric, credential or external service, and (if the X3 scope below is kept) no request. One independent post-change reviewer checks the gates.

### Verified facts this section relies on

| # | Fact | Evidence |
|---|---|---|
| X1 | `fulton.fetch_parcels` returns only parcels with `LandAcres` in [min_acres, max_acres] (5-50 by default), and only in the chosen `jurisdictions`: the filter runs inside the fetch loop. Multipart rows are unioned by `ParcelID`, so ids are unique in its output. "The full parcel list" in `run.main` is therefore the 5-50 ac band of the searched jurisdictions, not the county roll. | `fulton.py` `fetch_parcels` (the `where` clause, the `jurisdictions` check, the `by_id` dict) |
| X2 | `build_eval` builds the eval pool with the same `fulton.fetch_parcels(5, 50, juris, url=SNAP_AS_OF)` and imports `checks`, `layers`, `screens` and `fulton` (and through them `owners`). A behaviour change in `fetch_parcels`, `owners.normalize` or `owners.owner_type` would change the next eval rebuild even though those files are not on the `EVAL_FILES` list. | `autoresearch/build_eval.py` lines 56, 125 |
| X3 | Every member of a cluster is itself 5-50 ac (X1). An adjacent group of two or more therefore sums to at least 10 ac; "sums into 5-50" here means "at most `max_acres`". Small neighbours (under 5 ac) that together make a site are invisible in phase 1. Seeing them needs a second, wider parcel fetch, which is a new request load on the county server (SR-11). | X1; arithmetic |
| X4 | `owner` is the raw tax-roll `Owner`. `owner_mailing` is `"<OwnerAddr1>, <OwnerAddr2>"`, with either part dropped when blank, so a parcel with no street line has only "CITY ST ZIP". `owner_type` is a function of the name alone. | `fulton.py` lines 129-149; `owners.owner_type` |
| X5 | Because `owner_type` depends only on the name, an exact-name cluster has ONE owner type. Clustering before the attribute rejects therefore cannot "widen the developer exclusion" (the background analysis's hope). A recognised developer's siblings are already rejected under the same name. What pre-reject clustering does add is siblings rejected for other reasons: built on, tax-exempt, `pass` / `listed` decisions, and later the site and slope screens. | `owners.py`; `screens.attribute_rejects` |
| X6 | `scoring.score(p)` passes the live dict to `rank.learned_score(p)` (F8 V1). A private key is not a guard (F8 V2). `rank.py` may import only `__future__`, `math` and `typing` (`RANK_IMPORTS`), so it cannot reach `run.main`'s locals or a new module. A side structure local to `run.main` is out of its reach. | `scoring.py` line 56; `evaluate.py` lines 83, 213-280 |
| X7 | `p["flags"]` is first created at line 230, after `p.update(scoring.score(p))`, and the slope-skipped flag is appended at lines 231-233. Every reject step (attribute, site, slope) has run by then, so each sibling's final status (ranked, or screened out with its first reason) is known in that loop. Rank numbers are assigned after the sort at lines 236-238. | `run.py` lines 170-238 |
| X8 | `flags` reaches `ranked.csv` (joined with `"; "` by `report._cell`), `ranked.geojson` / `map.html` properties, and the brief's "## Watch-outs". `rejected.csv` has a fixed seven-column header with no flags column. Nothing reads `flags` back into a score. The F14 snapshot is taken from the `FEATURES` allow-list before scoring, so it never sees flags. | `report.py` lines 14-21, 28-60, 131-132; `snapshot.take` |
| X9 | The offline fixture `county_parcel()` gives every parcel the same default owner (`TEST OWNER ALPHA`) and the same mailing (`1 TEST ST, ATLANTA GA 30303`), 20.0 acres each. Every multi-parcel offline `run.main` in the existing suite becomes ONE cluster under any name+address rule, and squares placed side by side become adjacent. Existing flag assertions are `assertIn` / `assertNotIn`. Two run-to-run comparisons (`test_earthwork` ~410, `test_decisions` ~174) compare `flags` between two runs that would both carry the same new lines. | `tests/test_integrity.py` `county_parcel`, lines 566-812; grep of `flags` in tests |
| X10 | Geometries are WGS84 degrees. `geo.LocalProjection` gives flat metres around a centroid, and `geo.FT_PER_M` exists. A distance in degrees is not feet, and one degree of longitude is about 0.83 of a degree of latitude at 33.7°N. | `geo.py` |
| X11 | `--limit` slices the sorted list at lines 103-104, inside the requirement's "before attribute rejects" window. A cluster computed after the slice depends on the trial size; one computed before it can name siblings that are neither ranked nor rejected in this run. | `run.py` lines 102-105 |

### HIGH

**F2-1. Cluster facts must not reach `rank.py` (X6; F8-1 / F14-1 analog).** Portfolio size and adjacent-holding acres are seller-side signals the loop would love: big holders and land-bankers read as motivated or unmotivated sellers. The eval records carry none of them, so a live `p["portfolio_parcels"]` would be a live/eval split the loop could learn from. The background analysis's integration note says to "`p.update()` the cluster fields" right after the attribute screens. **That shortcut is a no-go.**

*Mitigation (structural):*
- `owner_clusters` returns a plain structure that lives in a `run.main` local, keyed by `parcel_id`. That key is safe here because `fetch_parcels` dedups by id (X1); still assert uniqueness and exclude duplicates with a counted note if a future county adapter breaks it. Nothing is written to any `p` before scoring.
- The flag and brief lines are built in the post-score loop (X7), from that local structure plus each sibling's final status, and appended to `p["flags"]` there.
- If brief-only fields are wanted (e.g. a sibling table), attach them after the sort, in the same place F8 attaches earthwork (`run.py` lines 239-243), under keys the static guard names.
- No change to `scoring.py`, `rank.py`, `screens.py` or `snapshot.py`.

*Guard (mirrors F8 G1/G2 and F14 H1):*
- (a) **Behavioural spy test.** An offline `run.main` with a fixture of distinct owners that holds one adjacent same-owner pair, one non-adjacent same-owner pair and singletons. Wrap `rank.learned_score`, `scoring.fixed_score`, `screens.site_rejects` and `screens.flags` to record `set(p)`. Assert that no recorded key set contains a cluster key, AND that `ranked.csv`'s `flags` carry the new lines for exactly the expected parcels. Run it with slope on and `--no-slope`.
- (b) **Static defence in depth.** No string constant in `rank.py` or `scoring.py` contains `cluster`, `portfolio`, `assembl` or `same_owner_adj` (whatever the chosen key stems are; pin them as constants in `owner_clusters.py` and import them into the test). Today's `rank.py` has `rail-adjacent` in a `why` string, so do NOT use `adjacen` as a stem. The docstring states the computed-string limit, as F8's does.
- (c) **Scores and order unchanged.** For the same fixture, rank, score, `why_site` and `why_seller` are identical with clustering on and off (monkeypatch the cluster call to return nothing). Only `flags` and the brief differ.

**F2-2. Over-merging unrelated owners makes false "assemblage" claims (X4).** An assemblage flag sends the operator to one seller for a combined site. A wrong merge wastes an approach, and worse, it states a fact about who owns what. Name-only fuzzy rules (`same_owner`'s 29-character truncation prefix rule and its loose people rule) are exactly the wrong tool here, as the requirement says.

*Mitigation:*
- **Exact equality of two keys**, no fuzzy step, no union-find over pairwise similarity. Clusters are then equivalence classes of `(name_key, address_key)`, so there is nothing to chain.
- `name_key`: `owners.normalize(owner)` (upper case, commas and periods to spaces, whitespace collapsed), then collapse the spaced legal forms (`L L C` → `LLC`, `L P` → `LP`, `L L L P` → `LLLP`). Do **not** drop legal forms, role words or numbers. `FUND I` / `FUND II` and `X LLC` / `X LP` stay apart.
- `address_key`: normalize `owner_mailing` the same way, and cut a ZIP+4 to its 5 digits. Keep suite / unit / `STE` numbers. Different suites in one office building are different tenants; the background note's "minus suite/unit" is rejected.
- **No key, no cluster:** a parcel joins nothing when the name is blank or a placeholder (`UNKNOWN`, `OWNER UNKNOWN`, `NOT AVAILABLE`, `CURRENT OWNER`, and a constant list in the module), or when the mailing has no street line (only "CITY ST ZIP", X4), or no ZIP at all.
- Do not import or call `same_owner` / `_same_name` from the new module. A test asserts that.
- Do not edit `owners.py` or `fulton.py` (X2). Reading `owners.normalize` is fine; changing it changes the next eval rebuild.

*Accepted under-merge (document it):* one owner's different spellings, different mailing addresses, and per-site SPE names (`XYZ 1 LLC`, `XYZ 2 LLC` at one address) do not cluster. A weaker "same mailing address, different owner name" line would catch the SPE case, but it is a different claim and needs its own review. It is out of phase 1.

*Guard:* tests for the following.
- Each normalisation step, case by case: spacing, punctuation, spaced legal forms, ZIP+4.
- A pair matching on name with different addresses, and a pair matching on address with different names: neither clusters.
- `FUND I` / `FUND II` at one address: no cluster.
- Suite 100 / suite 200: no cluster.
- Blank, placeholder and city-only mailings: singletons.
- Two `same_owner`-true but key-different names (a truncated name, initials): no cluster. This shows the name-only rules were not reused.

### MEDIUM

**F2-3. "Full parcel list" is narrower than it sounds (X1, X3, X5, X11).** The flag text must not overclaim.
- Portfolio counts cover only the 5-50 ac band in the searched jurisdictions. The owner may hold far more.
- Assemblages of small (under 5 ac) parcels are invisible.
- Pre-reject clustering cannot widen the developer exclusion (X5).

*Mitigation:*
- **Wording**, in substance: "same owner name and mailing address on the tax roll also holds N other 5-50 ac parcels in the searched area (M ac)". Then: "adjacent to K of them (touching or within F ft): T ac together". Name the band and the area from the run's settings, never "the owner's portfolio" or "the owner owns".
- **Cluster before the `--limit` slice** (on `parcels` right after the sort at line 102), so trial runs give the same portfolio counts as full runs. A sibling outside the trial reads "not screened in this trial run". A test pins counts equal with and without `--limit`.
- **No second fetch in phase 1.** Widening to small neighbours is phase 2: a new narrow request (SR-11), a cache entry, its own review. Do not change `fetch_parcels`' `where` or signature (X2).
- README: a short "Owner clusters" paragraph with these three limits and the X5 point.

**F2-4. Adjacency in the right units, with the right meaning (X10).**
- Measure in metres. Project each cluster's geometries with one `LocalProjection.around(<union or first member>)` (clusters are local; a county-wide cluster spans under about 60 km, where the equirectangular error is well under 1%), then `a.distance(b) <= gap_m`.
- Do not compare degree distances to a foot threshold.
- New settings key with a code default, e.g. `[owners] adjacency_gap_ft = 10`, read with `.get(..., 10)` so an old settings file still runs. The "few feet" bridges digitizing slivers on the county map. It deliberately does NOT bridge a road right-of-way (about 40-80 ft). Say so in the flag ("touching or within 10 ft"). Do not call the group "contiguous" or "one site".
- A shared corner point is distance 0. Phase 1 counts it, and the wording ("touching") stays true. Note in the README that a corner touch is not usable frontage.
- Group adjacent members into connected components (union-find or BFS inside a cluster only). Never across clusters.
- **"Assemblage"** = a component of two or more parcels whose summed tax-record `acres` lie in [`min_acres`, `max_acres`] from the run's `[area]` settings (not a hardcoded 5/50). A component above `max_acres` is reported as an adjacent holding "larger than the search band", not as an assemblage. Sum the tax-record `acres` (the band's own basis), and when any member has `acreage_mismatch`, say "acres from the tax record; the map disagrees for some".

*Guard:* tests for the following.
- Two squares sharing an edge: adjacent.
- A 5 ft gap (adjacent) and a 50 ft gap (not), built in metres through `LocalProjection`, and an east-west vs a north-south gap of equal feet giving the same answer.
- A corner touch.
- A chain A-B-C where A and C don't touch: one component.
- A 3 × 20 ac component (60 ac): "larger than the search band". 2 × 20 ac: assemblage.
- A settings file without `[owners]`.

**F2-5. Cost and failure modes on real data.** Clusters are small except for big public holders, which are rejected later but are clustered first (pre-reject). Pairwise distance inside a cluster of k members is O(k²): k = 500 is about 125 k GEOS calls.
- Use `shapely.STRtree` within any cluster above a small k (or always). Query with the gap-buffered envelope, then confirm with `distance`.
- Geometry from `fetch_parcels` is non-empty and `make_valid`'d, but it can be a `GeometryCollection` after a multipart union. `distance` handles that.
- A `shapely.errors.GEOSException` on one parcel marks that parcel "adjacency not checked". It still counts in the portfolio, and a summary note counts such parcels. Do not wrap the whole feature in a broad `except Exception`: a bug must fail the tests, not vanish.
- The clustering is informational. If it cannot run at all (e.g. no parcel has a key), the run continues with a note. It never calls `stop()`, which is for required sources.

*Guard:* a synthetic 2,000-parcel single-cluster grid finishes in under a few seconds in the test. One parcel with a deliberately broken geometry (patched `distance` raising `GEOSException`) gives the "not checked" count, and the rest of the cluster still groups.

**F2-6. Flag and brief size and content (X8; SR-8 analog).**
- A big holder could list hundreds of sibling ids in one `ranked.csv` cell.
  - Cap the listed sibling ids at 5 per line ("and N more").
  - Order them deterministically: adjacent first, then by `parcel_id`.
  - Give counts and acres in full.
- Each listed sibling shows its status:
  - ranked `#rank`;
  - screened out, with its first reason through `run.reason_key`;
  - "not screened in this trial run".
  - A sibling the operator marked `pass` / `listed` reads as such: that comes for free from the reject reason.
- Do not repeat the owner name (it is the same owner, already in the row) and no mailing in the flag. Ids and owner details are already in the gitignored `output/`.
- Phase 1 needs no new `RANKED_COLUMNS`; the lines go into `flags` and the brief. If columns are added anyway, put them after `flags`, record the schema change in the decision note (F8-8 analog), and keep `rejected.csv`'s header unchanged.
- Map popups take `flags` already; the existing escaping covers them. Add one test with an id holding `<` / `"` / `;` through `map.html` and the CSV cell.

*Guard:* the cap test (12 siblings give 5 ids + "and 7 more"), the status texts, determinism (two runs give byte-equal `flags`), and the escaping test.

**F2-7. Existing tests change shape silently (X9).** Every multi-parcel fixture run becomes one cluster, often with adjacency, because all fixture parcels share owner and mailing. Most assertions are `assertIn`, so they still pass. The new lines nonetheless appear in every fixture brief, which hides whether a test is exercising what it thinks it is.

*Mitigation:*
- Do not change `county_parcel`'s defaults: that would rewrite every existing test's premise.
- Build the F2 tests with explicit distinct owners and mailings.
- Run the full screener suite and list in the code writer response every existing test whose `flags` or brief output changed, and why each still holds.

### LOW

**F2-8. Eval and dependency boundary (SR-10, SR-15 analog).**
- `owner_clusters.py` imports only stdlib, `shapely`, `.geo` and (read-only) `.owners.normalize`.
- `build_eval.py` / `evaluate.py` do not import it.
- `requirements.txt` is unchanged.
- `python -m screener.autoresearch.evaluate` gives the same output line before and after, and `rank_import_problems() == []`.
- `git diff --stat` touches no eval file, nor `rank.py`, `scoring.py`, `screens.py`, `snapshot.py`, `owners.py`, `fulton.py`, `build_eval.py`, `autoresearch/data/` or `PROGRAM.md`.
- `settings.toml` only gains the new `[owners]` key(s), no default changes.

**F2-9. No requests (SR-11, SR-6).** Phase 1 reads only what `fetch_parcels` returned. The offline run's request count per URL is unchanged; assert it in the spy-run test. No new runtime file is written, so `.gitignore` needs nothing (F14-2 does not apply).

**F2-10. Determinism.** Cluster ids (if exposed at all) come from the sorted member ids, never from dict or set iteration order or `id()`. The same input gives byte-identical `ranked.csv` (already pinned by X9's run-to-run comparisons once both carry the lines).

**F2-11. Not a rank feature, and the future path.** If the operator later wants portfolio or assemblage in the score, the path is a human eval step. `build_eval` would compute the same exact-key clusters on the 2016 snapshot (owner and mailing exist there) and add a FEATURES entry, then rebuild with `--new-baseline`. Say this in the README so nobody "just" reads the flag text from `rank.py`. Do NOT edit `PROGRAM.md` here (F14-10 reasoning).

**F2-12. Counts-only live evidence (SR-8 analog).** If the one allowed `--limit 30` live run happens, record only:
- parcels fetched;
- clusters with two or more members, and parcels in them;
- ranked parcels carrying a portfolio line, and those carrying an assemblage line;
- components above the band;
- the largest cluster size (a count);
- "adjacency not checked" count;
- clustering wall time;
- the identical-evaluate check.

No ids, owners or addresses. Clustering runs on the full pre-limit list (F2-3), so the trial run still exercises the whole band, at no extra request cost.

### Go / no-go gates for F2 (all must hold before the decision note says go)

| Gate | Condition |
|---|---|
| J1 | Spy test: with slope on and `--no-slope`, no key set seen by `rank.learned_score`, `scoring.fixed_score`, `screens.site_rejects` or `screens.flags` holds a cluster key. The expected parcels' `flags` carry the new lines. Rank, score, `why_site` and `why_seller` are identical with clustering on and off (F2-1 a, c). |
| J2 | Static test: no string constant in `rank.py` or `scoring.py` contains the cluster key stems (imported from the module, not `adjacen`), with the computed-string limit stated (F2-1 b). |
| J3 | Exact two-key matching: every F2-2 case, including the no-key singletons and the `same_owner`-true-but-no-cluster pair. The new module does not reference `same_owner`. `owners.py` and `fulton.py` are unchanged (F2-2, X2). |
| J4 | Adjacency in metres: the edge, 5 ft / 50 ft, east-west vs north-south, corner, chain and band cases. The `[owners]` key is optional with a code default (F2-4). |
| J5 | Clustering runs on the pre-limit list: portfolio counts are equal with and without `--limit`, and out-of-trial siblings are labelled. The wording names band, area and gap and never says "owns" or "contiguous" (F2-3). |
| J6 | The 2,000-parcel timing test, the GEOS-error "not checked" path with a counted note, and no `stop()` on clustering failure (F2-5). |
| J7 | Flag cap, statuses, determinism and escaping. `rejected.csv` header unchanged. Any new `RANKED_COLUMNS` placed after `flags` and named in the decision note (F2-6). |
| J8 | No extra request; `requirements.txt` unchanged; the evaluate output line is identical to base; `rank_import_problems() == []`; the diff stays inside the F2-8 list (F2-8, F2-9). |
| J9 | Both suites green, with counts in the decision note: root about 1744 OK at base, screener 134 OK at base plus the F8 and F14 additions. The code writer response lists every existing test whose output changed (F2-7). README "Owner clusters" paragraph per F2-3, F2-4 and F2-11. The decision note restates the operator answers. |

**No-go triggers:**
- Any cluster, portfolio or assemblage value on `p` before scoring is done.
- Fuzzy or `same_owner`-based matching, or name-only / address-only clustering.
- A change to `fetch_parcels`, `owners.py`, any eval file, `rank.py`, `scoring.py`, `screens.py` or a `settings.toml` default.
- A new request or dependency.
- The cluster facts wired into a score, a screen, a reject or `screens.flags`.
- Flag wording that claims ownership beyond the tax-roll name and address, or contiguity beyond the measured gap.

**Verdict: GO with conditions J1–J9.**

Standing risks:
- SR-6: offline fixtures with distinct owners.
- SR-8 analog: no owner names in the flags; counts-only evidence.
- SR-10: stdlib + shapely only.
- SR-11: no new requests; the small-neighbour fetch is deferred to phase 2.
- SR-15 / SR-16 analogs: the eval boundary is unchanged. Cluster facts stay in a `run.main` local until after scoring, and the human read of `exp:` diffs is the backstop.

---

## F6 — Constraint-aware fit (largest open-ground part + a copied rotated envelope fitter)

**Requirement (as relayed):**
- `largest_open_acres`: the largest contiguous open-ground polygon after the flood, wetland, water, easement, protected and steep subtraction (`checks.map_checks` keeps `p["_open_ground"]`).
- A rotated-rectangle fitter COPIED (not imported; CLAUDE.md scope note) from root `site_fit.fit_lengths`, using the real prototypes from `building_prototypes.json` (read-only).
- Report the largest prototype whose envelope fits on open ground, as a brief fact and a `ranked.csv` column AFTER scoring. It is not a screen.

The operator answers recorded at the top of this document apply unchanged. F6 is a screener human edit. It changes no eval file, so there is no new eval baseline. It is reviewed at Tier 1 (one independent reviewer after the code). It touches no root file. `building_prototypes.json` and `site_fit.py` stay byte-identical (SR-1; the root invariant forbids any edit to the prototypes file).

### Verified facts this section relies on

| # | Fact | Evidence |
|---|---|---|
| Y1 | Envelope depth = `W + sides × (court + trailer)`, with sides = 2 for `cross`, else 1. **front** and **rear**: 260 + 130 + 55 = **445 ft**. **cross**: 520 + 2 × (130 + 55) = **890 ft**. Lengths step by `step_ft` = 40 from `min_length_ft` up to `L`. front/rear: 400, 440, …, 640 (7 lengths, 104,000–166,400 SF). cross: 600, 640, …, 1000 (11 lengths, 312,000–520,000 SF). front and rear share one depth, so root fits them as one spec. The relayed numbers are correct. | `building_prototypes.json`; `site_fit.envelope_depth`, `_lattice`, `_fits_for` |
| Y2 | Root fits the envelope inside the **setback-buffered** buildable area (`default_setback_ft` = 25, or the zoning setback), capped by any coverage cap. Car parking, drives, detention and circulation are not modelled (`fit_rule`). The fit is a **lower bound**: a fixed 2° angle grid plus the 32 longest edges (≥ 30 ft), vertex-event and 64-step y positions, and exact even-odd containment with `TOL_FT` = 0.01. "No fit found" is therefore not proof that nothing fits. | `building_prototypes.json` `fit_rule`; `site_fit.py` lines 100-131, 426-616 |
| Y3 | Root normalises rings before fitting. It uses open rings rounded to 0.01 ft and parts sorted by (outer area desc, first vertex), shifted to a floor-of-min origin (`_rings_from_geojson`, `_fits_for`). It simplifies above 400 vertices (1 ft tolerance, inside an extra 2 × tol inward buffer, so the result stays inside) and refuses to search above `_MAX_FIT_VERTICES` = 600. | `site_fit.py` lines 125-131, 980-1046, 1207-1250 |
| Y4 | **Steep ground is not geometry in the screener.** `slope_stats` returns histogram shares only. `usable_after_slope` scales acres by `(1 - steep%)`. `_open_ground` = parcel minus the union of flood (`SFHA_TF = 'T'`, floodway included), NWI wetland, NWI water, NCED easement and PAD-US protected polygons. Nothing locates steep pixels, so the requirement's "steep subtraction" cannot be done geometrically with the current data. | `checks.py` lines 82-105, 201-245; `layers.py` line 34 |
| Y5 | `map_checks` sets `p["_open_ground"] = open_ground if not open_ground.is_empty else g`. A parcel whose constraints cover all of it therefore carries the **whole parcel** as its "open ground". The fallback is detectable: in that case `p["_open_ground"] is p["geometry"]` while `constrained_pct > 0`. With no constraints at all it is also `g`, with `constrained_pct == 0`. | `checks.py` lines 98-105 |
| Y6 | `g.difference(...)` can return a Polygon, a MultiPolygon or a GeometryCollection that holds slivers, lines or points. Coordinates are WGS84 degrees. `LocalProjection.around(g)` gives metres (equirectangular around the centroid), and `geo.FT_PER_M` and `geo.acres` exist. | `checks.py`; `geo.py` lines 71-96 |
| Y7 | `_open_ground` and `geometry` are shapely objects on `p` while `scoring.score(p)` runs. Eval records hold `build_eval.FEATURES` only, with no geometry. `rank_problems` refuses `_`-prefixed *attributes*, not public ones (`.area`, `.geoms`) or string subscripts (F8 V2). Today's `rank.py` could therefore already measure open-ground parts on live data only. That exposure **pre-exists** F6. F6 must not widen it. | `build_eval.py` line 80; `evaluate.rank_problems`; probe in F8 V2 |
| Y8 | The earthwork precedent is the after-scoring pattern this item should copy: a `run.main` local keyed by `id(p)`, attached after scores, flags and the sort. F2 then appends flags in the same post-sort block. `report.RANKED_COLUMNS` already carries `earthwork_cy` and `mean_cut_fill_ft` after `relief_ft`. `rejected.csv` has a fixed seven-column header. | `run.py` lines 179-243; `report.py` lines 14-21, 48-54 |
| Y9 | Probe, read-only, of root `fit_lengths` on both specs (445 / 890) in feet: the cost is negligible when a full-size envelope fits (early exit). The worst case is a shape where nothing fits, because every angle is then tried. Timings: a 40 ac square with 40 wetland-like holes (921 vertices, 18 rings, no fit) took 0.45 s. A 1,500-vertex jagged ring took 1.9 s. A 300 ft × 3,000 ft wiggly strip (600 vertices, no fit) took 0.8 s. Pure Python is GIL-bound, so a thread pool does not help. | probe in this review (no repo file written) |
| Y10 | The CLAUDE.md scope note says `screener/` "shares no code, tables or files with the system below". The screener reads no root file today (grep: no `site_fit` or `building_prototypes` reference under `screener/`). The precedent for a copy is `zoning_import.py` line 110 ("copied, not imported, from the screener"). | `CLAUDE.md`; grep |

### HIGH

**F6-1. Live-only leak into `rank.py` (Y7, Y8; F8-1 / F2-1 analog).** A fit result and an open-part acreage are exactly what a learned rank would grab: "a cross-dock fits" is a strong live-only signal, and the eval records carry none of it. The tempting place is `map_checks`, right beside `_open_ground`. **Computing `largest_open_acres` there and putting it on `p` is a no-go**, even though the background analysis proposes it (its integration point 1). Doing so would put a live-only number on `p` before `site_rejects`, `scoring.score` and `screens.flags` run.

*Mitigation (structural):*
- Compute both facts after the final sort, in the post-sort block where earthwork is attached (`run.py` around line 239). Read `p["_open_ground"]` there.
- Keep any intermediate (parts, rings, placement) in a `run.main` local or the new module's return value. Never put it on `p`.
- Attach only the report fields, and only after scores, flags and the sort.
- Do not change `scoring.py`, `rank.py`, `screens.py`, `snapshot.py` or any eval file.
- Do not store a parts list or a largest-part geometry on `p` (Y7): that would make the pre-existing geometry exposure easier to use.

*Guard:*
- (a) **Spy test**, the decisive one. Copy F8 G1 / F2 J1: an offline `run.main` with slope on and with `--no-slope`. Wrap `rank.learned_score`, `scoring.fixed_score`, `screens.site_rejects` and `screens.flags` to record `set(p)`. Assert that no recorded key set holds an F6 key, and that `ranked.csv` then carries the populated columns for the same parcels.
- (b) **Scores and order unchanged.** Rank, score, `why_site`, `why_seller` and `flags` are identical with the fit on and off (monkeypatch the fit to return nothing). Only the new columns and the brief line differ.
- (c) **Static defence in depth.** No string constant or name in `rank.py` or `scoring.py` contains the F6 key stems. Define the stems once as constants in the new module and import them into the test. Candidate stems: `largest_open`, `prototype`, `envelope`, and the fit column stem. Do NOT use a bare `fit` or `open`: they are too short and too likely to collide later (`benefit`, `opens`). The docstring states the computed-string limit, as F8's does.

**F6-2. The fallback turns a fully constrained parcel into "all open ground" (Y5).** Read naively, `p["_open_ground"]` is the whole parcel when the constraints cover all of it. Both facts would then report the parcel's full map area and a cross-dock fit on a floodplain. Ranked survivors usually have `usable_acres ≥ min_usable_acres` (4 by default), so the case is rare. But `usable_acres` comes from tax-record `acres`, so a parcel with an acreage mismatch can survive with a map polygon that is fully constrained.

*Mitigation:*
- Treat `p["_open_ground"] is p["geometry"] and p["constrained_pct"] > 0` as "no open ground": `largest_open_acres = 0.0`, and the fit reads "no fit found (no open ground on the parcel map)".
- Better still, have the helper take `(geometry, open_ground, constrained_pct)` explicitly, so the rule is stated in one place.
- Do not change `map_checks`' fallback itself. Slope relies on it (`too_small_for_slope`, `slope_stats`), and changing it would move slope fields and the steep screen.

*Guard:* a fixture parcel fully inside a flood polygon (constraints = 100%) gives 0.0 and "no fit found". A parcel with no constraints gives the whole-parcel part. The slope fields of both are unchanged from base.

**F6-3. Over-claiming what is subtracted and what "fits" means (Y2, Y4).**

Steep ground: the requirement asks for "steep subtraction" in the polygon, which the data cannot give (Y4). A fit on 20 open acres of 25% slope would read as buildable.
- Do not invent a geometric steep mask, for example by shrinking the polygon by `steep_pct`. A shrunk polygon has no location meaning.
- The column and the brief say what the open ground excludes: "floodplain (FEMA SFHA), mapped wetlands and water (NWI), conservation easements (NCED) and protected land (PAD-US)". They also say steep ground is NOT located: "slope is applied only as a share (`usable_after_slope`); steep ground may lie inside the open area".
- A geometric steep mask is a later item. It would need 3DEP sampling through `arcgis.py`, under its own review (SR-11).

Easements: only conservation easements are mapped. Utility, pipeline and access easements are not in any free layer (README). The wording must not say "easements subtracted" without "conservation".

"Fits": the root envelope is building plus truck court plus trailer parking per dock side. There is no car parking, no drive aisles, no detention, no frontage or access check, no coverage cap (the screener has no coverage rules), and no zoning setback beyond the one chosen in F6-4.
- The wording is "envelope (building + truck court + trailer parking) fits", never "site works", "buildable for" or "supports".
- The fit is a lower bound (Y2), so its absence reads "no fit found", never "does not fit" or "too small".

*Guard:* a README "Site fit" paragraph with these limits. Brief-line tests pin the "no fit found" wording and the exclusion list. A test asserts the brief never contains "does not fit" (hand-written wording only; no assertion on free-form operator text).

### MEDIUM

**F6-4. Setbacks: the root semantics are inside a setback (Y2).** A fit on raw open ground places the envelope edge on the lot line. That runs 25 ft or more past where root, and the operator's own tool, would place it, and it overstates the result against root's BTS figures for the same parcel.

*Mitigation, either of two (the code writer picks one and states it):*
- (preferred) Fit on `open_ground ∩ parcel.buffer(-setback)`, with `setback` = a COPIED `default_setback_ft` (25). The setback is measured inward from the lot line only, not from the constraint edges, which matches root (root subtracts hazards from the setback-buffered area). `largest_open_acres` stays the unbuffered open ground, because that is what "open acres" means.
- Or fit on raw open ground and label it "no setback applied" in the column docs and the brief.
- Either way, the buffer is done in the projected metre plane (Y6), never in degrees. The brief names the setback used.

*Guard:* a 30 ac square where front-load fits without the setback but not with it, or the reverse at one lattice step. This pins whichever choice was made.

**F6-5. The copy must be faithful, and must stay a copy (Y3, Y10; SR-1 / scope note).**
- **No import of root** (`site_fit`, `zoning`, `prepare` or anything outside `screener/`) from screener runtime code. A static test asserts that the new module and `run.py` import nothing outside `screener` (except stdlib and shapely).
- **No runtime read of root `building_prototypes.json`.** The requirement says "read building_prototypes.json read-only". Reading it at run time would make the screener share a file with the root system, contrary to the scope note. It would also break a screener run from an extracted copy.
  - Copy the needed numbers into the new screener module as constants, with a provenance comment (file, `as_of` 2026-11-05, `source`): per prototype `W`, `L`, `min_length_ft`, `court`, `trailer`, `trailerOn`, `type`, `label`, plus `step_ft` and `default_setback_ft`.
  - A **drift test** in `screener/tests` reads root `building_prototypes.json` read-only. It is skipped when the file is absent. It asserts that the copied numbers equal it, so an operator edit to the root file fails the screener suite loudly instead of drifting silently.
  - Do not put the envelope in `settings.toml`. An operator-editable copy would drift by design, and the drift test would then fight legitimate edits. An optional on/off or setback key is fine.
- **Port only what the fit needs:** `fit_lengths`, `_angles`, `_slab_gaps`, `_probe_chords`, `_bad_prefix`, `_candidate_ys`, `_inside`, `_ring_area`, `envelope_depth` and `_lattice`, with the same constants (`TOL_FT`, `_ANGLE_GRID_DEG`, `_EDGE_ANGLES_MAX`, `_EDGE_ANGLE_MIN_LEN_FT`, `_Y_SAMPLES`, `_PROBE_FT`). Keep root's ring normalisation (Y3): round to 0.01 ft, drop the closing vertex, sort parts by (outer area desc, first vertex), shift to the floor-of-min origin. Results then match root on the same feet-plane rings, and float error stays bounded.
  - Do not port the cache, the SQL, the coverage-cap logic, the rules or adjacency code, or the dock and trailer counts (unless they are shown in the brief).
  - Do not "improve" the algorithm in the copy. Changing it would leave two fitters that disagree.
- **Equivalence evidence.** Golden tests on hand-made rings (square, rotated square, square with a hole, two-part MultiPolygon, L-shape, thin strip) pin the fitted length per depth.
  - The code writer runs root `fit_lengths` once on the same rings, OUT of the screener tests, and records in the code writer response that the values match.
  - An optional differential test that imports root `site_fit` is acceptable only if it is skipped when root is unavailable and the suite is green without it. Golden values are the gate.

**F6-6. Cost and determinism (Y3, Y9; F2-5 analog).** Pure-Python fitting is GIL-bound. At about 0.5–2 s per hard parcel, a run with a thousand or more survivors could add many minutes to a run whose re-runs take "a few minutes" (README).

*Mitigation:*
- Bound the vertex count as root does. Above 400 vertices, simplify inside an extra inward buffer: buffer by -2 × tolerance in metres, then `simplify(tolerance)`, so the result stays inside the original. Above 600 vertices after that, the parcel gets "fit not checked: open-ground shape too complex" plus a counted run note.
- Drop parts that cannot hold the smallest envelope (a part whose minimum rotated rectangle is under 400 × 445 ft) before fitting. This is the cheap, safe pruning: a part smaller than the smallest envelope cannot contain it.
- **No wall-clock budget.** A time-based cutoff makes `ranked.csv` depend on machine speed (F2-10 determinism). If a cap is needed, use a deterministic one: an optional `[fit] max_parcels` key that fits the top N by rank, with the others "not checked (outside the top N)". Note the count.
- Log progress like slope does, every 100 parcels.
- The same input gives byte-identical columns: no set or dict iteration order in the output path.

*Guard:*
- A timing test on a synthetic worst case (the 40-hole square, or a 600-vertex no-fit strip) × 50 parcels finishes within a stated bound.
- A complexity-cap test: a 2,000-vertex ring gives "not checked: too complex" plus the note.
- A determinism test: two runs give byte-equal `ranked.csv`.
- If the one allowed live `--limit 30` run happens, its fit wall time is recorded (counts only).

**F6-7. Geometry and units (Y6).**
- Project with one `LocalProjection.around(geometry)` per parcel, the same projection `map_checks` uses. Convert metres to feet with `geo.FT_PER_M`, never a new literal. Compute acres with `geo.acres` on the projected part.
- Take Polygon parts only, from Polygon, MultiPolygon or GeometryCollection, with holes kept. Ignore lines and points.
- `largest_open_acres` = the area of the largest Polygon part (map acres), rounded to 0.1.
- When `acreage_mismatch` or `too_small_for_slope` applies, the brief adds "parcel map disagrees with the tax record; the fit uses the map shape". Do not skip these parcels silently.
- A `GEOSException` or other per-parcel geometry fault gives "not checked" plus a counted note. Do not wrap the feature in a broad `except Exception` (F2-5 reasoning: a bug must fail the tests). The fit never calls `stop()`.

*Guard:*
- A two-part open ground (12 ac + 8 ac) gives 12.0.
- A GeometryCollection holding a polygon plus a line gives the polygon's area.
- An east-west strip and a north-south strip of equal feet fit equally (degrees vs feet).
- A 45°-rotated rectangle fits at its full length (the rotation is exercised).
- A part with a hole that blocks the envelope gives "no fit found" or a shorter length.
- A patched GEOS error gives "not checked" plus the note, and the other parcels are unaffected.

**F6-8. Three states must stay distinguishable in the outputs (F8-7 analog).** The outputs must keep apart:
- *fits* (prototype, length × width, SF);
- *no fit found* (checked, nothing at the minimum length);
- *not checked* (fit off, too complex, geometry fault, outside a top-N cap).

An empty cell must never mean "no fit". 0 must never mean "not checked".

*Mitigation:*
- Use three columns, or an equivalent:
  - `largest_open_acres` (number; 0.0 when there is no open ground per F6-2; empty only when not computed);
  - a fit text column: `"cross-dock 800 x 520 ft"`, `"front/rear-load 640 x 260 ft"`, `"none found"`, or empty for not checked;
  - a numeric building-SF column: empty unless something fits.
- "Largest prototype" = the largest building SF that fits. Every cross-dock length (≥ 312,000 SF) beats every front-load length (≤ 166,400 SF), so take cross when any cross length fits, else front/rear.
- Name front and rear together, because they share a depth (Y1).
- The brief also says that a cross-dock fit implies the front-load fits. Do not claim that every prototype was fitted separately where one depth covers two.

*Guard:* one test per state, through both the CSV cell and the brief.

**F6-9. Output-schema change (F8-8 analog).** New `RANKED_COLUMNS` change the CSV header and the geojson and map properties.
- Place them together, directly after `usable_after_slope` or after `mean_cut_fill_ft`. The code writer chooses, and the decision note names the placement.
- `rejected.csv`'s header stays unchanged.
- The map popup escaping already covers the text cell. One test puts a fit text through `map.html` unchanged.
- Name the schema change in the decision note and the README `ranked.csv` row.

### LOW

**F6-10. Eval and dependency boundary (SR-10, SR-15 analog).**
- The new module imports only stdlib, `shapely` and `.geo`.
- `build_eval.py` and `evaluate.py` do not import it.
- `requirements.txt` is unchanged: no `lir` (a name trap: `pip install lir` installs an unrelated library), no numba, no opencv.
- The evaluate output line is identical to the one before the change, and `rank_import_problems() == []`.
- The diff touches no eval file, nor `rank.py`, `scoring.py`, `screens.py`, `snapshot.py`, `checks.map_checks`' existing keys, `PROGRAM.md`, `building_prototypes.json` or `site_fit.py`.
- `settings.toml` only gains optional new keys (for example `[fit] enabled` and `max_parcels`), each read with `.get(..., default)` so an old settings file still runs.

**F6-11. No requests (SR-11, SR-6).** The fit reads only geometry already in memory. The offline run's request count per URL is unchanged; assert it in the spy-run test. No new runtime file is written, so `.gitignore` needs nothing.

**F6-12. Not a screen, not a flag-into-score, and the future path.** The background analysis suggests a soft flag ("open ground too fragmented for a N SF building"). A flag is acceptable only if it is appended in the post-sort block, never through `screens.flags`, which is an eval file. Prefer the column and the brief line alone in this round. To make the fit a learned feature or a screen, a human would add it to `build_eval.FEATURES`, rebuild `data/` and take `--new-baseline`. Say that in the README so nobody "just" reads it from `rank.py`. Do NOT edit `PROGRAM.md`.

**F6-13. Pre-existing exposure, recorded and not widened (Y7).** `p["_open_ground"]` and `p["geometry"]` are already readable by `rank.py` through public shapely attributes, with no eval counterpart. That is outside this item, and fixing it would change `checks.py`'s slope path or the eval guard. Record it in the decision note as a residual for a later item (for example, moving `_open_ground` into a `run.main` local, which needs its own review of the slope path). F6 adds no new geometry key to `p`.

**F6-14. Counts-only live evidence (SR-8 analog).** If the one allowed `--limit 30` live run happens, record only:
- ranked parcels;
- counts by fit state (cross / front-rear / none found / not checked, with reasons);
- the median and maximum `largest_open_acres` as rounded numbers;
- the fit wall time;
- the identical-evaluate check.

Record no ids, owners or addresses.

### Go / no-go gates for F6 (all must hold before the decision note says go)

| Gate | Condition |
|---|---|
| K1 | Spy test: with slope on and `--no-slope`, no key set seen by `rank.learned_score`, `scoring.fixed_score`, `screens.site_rejects` or `screens.flags` holds an F6 key. The expected parcels' CSV cells are populated. Rank, score, `why_site`, `why_seller` and `flags` are identical with the fit on and off (F6-1 a, b). |
| K2 | Static test: no string constant or name in `rank.py` or `scoring.py` contains the F6 stems (imported from the module; not bare `fit` / `open`), with the computed-string limit stated (F6-1 c). |
| K3 | Fallback: a fully constrained parcel gives 0.0 open acres and "none found". An unconstrained parcel gives the whole part. Slope fields are unchanged from base on both (F6-2). |
| K4 | Wording: the exclusion list (with "conservation easements"), "steep ground is not located", "envelope (building + truck court + trailer parking)", "no fit found" (never "does not fit"), and the setback used. A README "Site fit" paragraph covers the F6-3 / F6-4 limits and the future eval path (F6-3, F6-4, F6-12). |
| K5 | Faithful copy: no screener runtime import of, or file read from, the root system. The prototype constants carry a provenance comment, and a drift test against root `building_prototypes.json` (skipped if absent) is green. Golden lengths are pinned for the F6-5 shapes, with depths 445 / 890 and lattices 400–640 / 600–1000 by 40, and the code writer response records that root `fit_lengths` gives the same values (F6-5, Y1). |
| K6 | Cost and determinism: vertex simplification stays inside the original. The > 600-vertex case gives "not checked" plus a counted note. The worst-case timing test passes. There is no wall-clock cutoff. Two runs give byte-equal `ranked.csv` (F6-6). |
| K7 | Geometry and units: the two-part, GeometryCollection, east-west vs north-south, 45° rotation, blocking-hole and GEOS-fault cases, all in projected metres converted with `FT_PER_M` (F6-7). |
| K8 | Three states, distinguishable in the CSV and the brief. Largest-SF selection (cross before front/rear). `rejected.csv` header unchanged. Column placement named in the decision note (F6-8, F6-9). |
| K9 | No extra request. `requirements.txt` unchanged. Evaluate output line identical to base, and `rank_import_problems() == []`. The diff stays inside the F6-10 list. Both suites green with counts in the decision note: root about 1744 OK at base, screener 134 at base plus the F8 / F14 / F2 additions. The code writer response lists every existing test whose output changed. The decision note restates the operator answers and the F6-13 residual (F6-10, F6-11, F6-13). |

**No-go triggers:**
- Any F6 value, part list or placement on `p` before scoring is done, or computed inside `map_checks`.
- A runtime import of root code, or a runtime read of `building_prototypes.json`.
- An edit to `building_prototypes.json`, `site_fit.py`, any eval file, `rank.py`, `scoring.py`, `screens.py` or a `settings.toml` default.
- A change to `map_checks`' `_open_ground` fallback or to any slope field.
- A geometric "steep subtraction" built from `steep_pct`, or wording that claims steep ground or non-conservation easements were removed.
- A wall-clock budget, a new dependency or a new request.
- The fit wired into a score, a screen or a reject.

**Verdict: GO with conditions K1–K9.** The requirement's "steep subtraction" is NOT deliverable geometrically with the current data (Y4). It is replaced by the F6-3 disclosure, and a steep mask is deferred to a later item with its own review.

Standing risks:
- SR-1: no root file is touched. `building_prototypes.json` is read only by a test, never at run time.
- SR-6: offline fixtures and hand-made rings only.
- SR-8 analog: counts-only evidence.
- SR-10: stdlib + shapely only.
- SR-11: no new requests.
- SR-15 / SR-16 analogs: the eval boundary is unchanged. F6 facts are attached after scoring, and the human read of `exp:` diffs is the backstop. The pre-existing geometry exposure (F6-13) is recorded, not widened.
