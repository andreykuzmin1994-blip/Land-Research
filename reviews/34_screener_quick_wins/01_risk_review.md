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
