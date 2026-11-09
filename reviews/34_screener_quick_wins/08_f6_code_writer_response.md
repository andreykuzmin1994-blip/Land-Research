# 08 — F6 constraint-aware fit: code writer response

Response to `01_risk_review.md` § F6. Base `22187d0` (PR 39 merged). No root
file, eval file, `rank.py`, `scoring.py`, `screens.py`, `snapshot.py`,
`checks.py`, `PROGRAM.md` or `requirements.txt` touched.

## What changed

| File | Change |
|---|---|
| `screener/sitefit.py` (new) | Copied prototypes + `fit_lengths` search; `fit_parcel(geometry, open_ground, constrained_pct)` → `Fit`; `brief_line`. Imports stdlib, shapely, `.geo` only. |
| `screener/run.py` | `fit_settings` (`[fit] enabled`, `max_parcels`; bad value → exit 2) and `attach_fits`, called right after the earthwork attach (post-sort). Counted "not checked" notes. |
| `screener/report.py` | Three `RANKED_COLUMNS` after `mean_cut_fill_ft`; "Building fit" brief line. |
| `screener/settings.toml` | New `[fit]` table (`enabled = true`, `max_parcels = 0`). No existing key changed. |
| `screener/README.md` | `ranked.csv` row, Tune-it bullet, "Site fit" paragraph (limits, lower bound, steep not located, future eval path). |
| `screener/tests/test_sitefit.py` (new) | 33 tests. |

## Decisions on the risk items

- **F6-1 / K1-K2.** Fit computed only in `attach_fits`, after scores, flags and the sort. Spies on `learned_score`, `fixed_score`, `site_rejects`, `flags` (slope on and `--no-slope`) see no key with a stem `largest_open` / `envelope`. Fit on vs patched off: rank, score, flags, why_site, why_seller identical; request count identical. Static stem guard on `rank.py` / `scoring.py`, with the computed-string limit self-tested.
- **Private keys, deviation from F6-1's "no geometry on p".** Per the operator's F6+F12 request, the fitted envelope (whole envelope, WGS84 Polygon) is attached post-sort as `p["_envelope_polygon"]`, and the `Fit` record as `p["_envelope_detail"]` (brief input). Both are after scoring, carry the stem (spy-covered), and are not written to the CSV/geojson. F12 draws the polygon.
- **F6-2 / K3.** `open_ground is geometry and constrained_pct > 0` → `0.0` acres, `none found` ("no open ground on the parcel map"). `map_checks` unchanged; the fit never writes to `p` before attach (asserted).
- **F6-3 / K4.** Brief wording: "envelope (building + truck court + trailer parking)", "lower bound", the exclusion list incl. "conservation easements (NCED)", "steep ground is not located", "no fit found … not proof that nothing fits"; "does not fit" / "too small" never appear (test). No steep mask.
- **F6-4.** Preferred option: fit on `open_ground ∩ parcel.buffer(-25 ft)` in the metre plane; `largest_open_acres` is the unbuffered part. Pinned by a 480 ft square (fits at setback 0, none found at 25 ft) and an envelope-inside-setback check.
- **F6-5 / K5.** Constants copied with provenance (`as_of` 2026-11-05). Drift tests read root `building_prototypes.json` and `site_fit.py` (as text, never imported; skipped if absent). Search code copied unchanged. Golden lengths on 9 shapes. **Equivalence:** root `site_fit.fit_lengths` was run once outside the suite on the same normalised rings: identical lengths **and** placements (angle, xc, y0) on all 9.
- **F6-6 / K6.** >400 vertices → `buffer(-2 ft).simplify(1 ft)`, result inside the original (test); >600 after → `not checked: open-ground shape too complex` + note. Parts pruned by area < 400×445 ft or hull min-width < 400 ft. No wall clock (AST test); optional deterministic `[fit] max_parcels` by rank. Worst case (36-hole 40 ac square, nothing fits): ~0.7 s/parcel, 50 parcels 33.7 s measured outside the suite; the suite runs 4 with a 20 s bound. Byte-equal `ranked.csv` across two runs.
- **F6-7 / K7.** One `LocalProjection.around(geometry)` per parcel, `FT_PER_M`, `geo.acres`. Tests: 12+8 ac → 12.0; collection polygon + line; E-W vs N-S strip equal; 45° rotated 1110×950 ft → cross-dock 1000; blocking hole drops cross to front/rear 640; GEOS fault → `not checked: geometry fault` + counted note, other parcels unaffected; a `ValueError` propagates. Map/tax mismatch adds "parcel map disagrees with the tax record, the fit uses the map shape".
- **F6-8 / K8.** `envelope_fit` is `cross-dock L x 520 ft` / `front/rear-load L x 260 ft` / `none found` / `not checked: <why>`; `envelope_building_sf` empty unless fits; `largest_open_acres` empty only when not computed. Cross tried first (always larger SF). `rejected.csv` header unchanged; text reaches `ranked.geojson` and `map.html`.
- **F6-9.** Placement: directly after `mean_cut_fill_ft`. Schema change named in the README `ranked.csv` row.
- **F6-10 / F6-11 / K9.** Evaluate output line identical before/after (`recall 0.059`, pool 2229); `rank_import_problems() == []`. No new dependency, no request, no runtime file.
- **F6-13 residual.** `_open_ground` / `geometry` remain readable by `rank.py` via shapely attributes (pre-existing, not widened by a pre-score key). Left for a later item.
- **F6-14.** No live run done.

## Tests

Screener suite: 289 tests OK (256 at base + 33 new). No existing test's output changed.
