# 09 — F6 constraint-aware fit: reviewer decision (Tier 1)

Fresh reviewer. I read `01_risk_review.md` § F6, `08_f6_code_writer_response.md`
and the actual diff (`git diff 22187d0..b390e3c`), then re-ran checks myself.

## Verdict: GO (K1–K9 hold), with the small fixes below

| Gate | What I checked |
|---|---|
| K1 leak | `attach_fits` runs after scores, flags, sort, rank and the earthwork attach. Nothing touches `p` earlier (the fit reads `geometry`, `_open_ground`, `constrained_pct` only). Spies (slope on and `--no-slope`) and on/off identity tests are present and pass. |
| K2 static | Stems `largest_open`, `envelope` imported from `sitefit.KEY_STEMS`; limit self-tested. |
| K3 fallback | `open_ground is geometry and constrained_pct > 0` gives 0.0 / `none found`; tested through real `map_checks`. Upstream `geo` runs `make_valid`, so GEOS faults stay rare. |
| K4 wording | Exclusion list, "steep ground is not located", "lower bound", "no fit found", 25 ft setback; "does not fit" never appears. |
| K5 copy | I compared the copied search against root `site_fit.py` by AST: `_sides`, `envelope_depth`, `_ring_area`, `_inside`, `_angles`, `_slab_gaps`, `_probe_chords`, `_bad_prefix`, `_candidate_ys` and `fit_lengths` are the same, apart from one parameter rename and one annotation. Ring normalisation and the lattice match root's `_rings_from_geojson`, `_fits_for` and `_lattice`, with no coverage cap. No root import and no file read at run time. |
| K6 cost | Simplify only above 400 vertices: `buffer(-2 ft)` and then `simplify(1 ft)`, so the result stays inside. Refused above 600. Pruning by area and hull width is a valid necessary condition. No wall clock; `max_parcels` cuts the run by rank. |
| K7 units | One `LocalProjection` per parcel, `FT_PER_M`, `geo.acres`. I checked the inverse projection: on 20 random polygons the envelope stays within the parcel and at least 25 ft from the lot line. |
| K8/K9 | Three states are kept apart. Columns sit after `mean_cut_fill_ft`. The `rejected.csv` header is unchanged, and `_envelope_polygon` does not reach the geojson or CSV. No new request and no new dependency. |

## Fixed in this step

1. **Drift test missed the search code.** Only the constants and the depth formula were drift-tested. I added `test_drift_against_root_search_code`. It AST-compares the 10 copied functions with root `site_fit.py`, read as text and skipped if absent. A mutation check confirms it fails on a one-token change.
2. **F12 envelope inside open ground was untested** (it was only checked against the bare parcel). The blocking-hole test now asserts that the drawn envelope avoids the hole and keeps the 25 ft setback.
3. **README `0.0` wording.** `0.0` also covers open ground that rounds below 0.05 ac, not only "constraints cover the whole map". Reworded.

## Accepted as is

- The private keys `_envelope_polygon` and `_envelope_detail` sit on `p` after the sort. This was operator-directed for F12. The deviation from F6-1's "no geometry on p" is acceptable because the keys are post-scoring, stem-named and spy-covered.
- The vertex cap counts all parts, not each ring as root does. That is stricter and safe.
- Residual F6-13: the pre-existing exposure of `_open_ground` and `geometry` to `rank.py` is not widened. It is left for a later item.
- The steep mask is deferred, as F6-3 says.

Screener suite: 290 tests OK. No live run.

## Post-change review

A later check found the README worst-case timing false (about 4.3 s at the
vertex cap, not under a second). Fixed in `10_f6_timing_fix.md`: README
states the measured 4 to 5 s, the timing test uses the near-cap shape, and a
README test pins the wording. No change to the fit, the cap or ranking; the
fit is still attached after the sort and the spy tests still pass. Suite: 291
tests OK.
