# 11 — F12 SVG site plan: code writer response

Base `b40f669` (F6 landed). Only `screener/` and this file touched; no root
file, `rank.py`, `scoring.py`, `screens.py`, `settings.toml` or
`requirements.txt` changed.

| File | Change |
|---|---|
| `screener/plan.py` (new) | `render(p) -> (svg, "")` or `(None, why)`. Standard library + shapely only. Draws parcel outline, constrained ground (parcel minus `_open_ground`; map_checks' whole-parcel fallback reads as all constrained), largest open part, the F6 envelope (`_envelope_polygon`) with prototype label and SF, a 1/2/5 scale bar, north arrow, legend. One `LocalProjection.around(geometry)` per parcel (sitefit's plane), metres to feet, y flipped. Every number via one `.1f` formatter (no `-0.0`). All text through `html.escape(quote=True)`. Reads only geometry, `_open_ground`, `constrained_pct`, rank, parcel_id and the F6 keys; writes nothing on `p`. GEOS fault gives "not drawn (geometry fault ...)". |
| `screener/report.py` | `write_all` renders after scoring and writes `briefs/<rank>_<safe id>.svg` beside the `.md` (same stem, same escape check). `brief()` gains optional `plan_file` / `plan_note`: a "## Site plan" section with the image and a caption, or "Not drawn (why)". Default call unchanged. |
| `screener/README.md` | `briefs/` row and a "Site plan" paragraph. |
| `screener/tests/test_plan.py` (new) | 20 tests. |
| `tests/test_integrity.py`, `tests/test_owner_clusters.py` | Two existing tests assumed `briefs/` held only `.md`; now filter by suffix (and count one `.svg` per brief). |

Missing / not-checked: no geometry, empty, line-only collection: no SVG and the
brief says "Not drawn (no parcel map shape)"; map shape under 10 ft: "too small
to draw"; never map-checked: plain outline with "Constraints: not checked";
fit off / none found / not checked are spelled out in the legend.

Leak rule. Order spy (slope on and `--no-slope`): every `learned_score`,
`fixed_score`, `site_rejects` and `flags` call happens before the first
`plan.render`; no spy sees a key with stem `plan` / `svg`, and no parcel holds
one afterwards (mutation check: a `p["_site_plan"]` write in `write_all` fails
two tests). With `render` patched off, `ranked.csv`, `ranked.geojson`,
`rejected.csv` and `summary.md` are byte-identical. Static backstop: `rank.py` /
`scoring.py` never name `plan` or `svg`.

Other tests: XML structure and classes, y flip (SW floodplain at bottom-left),
envelope inside the plan square, holes as evenodd sub-paths, determinism
(repeat, deep copy, two full runs byte-equal, SHA-256 pinned), no owner /
mailing / site address in the SVG (unit and end to end), escaping of a hostile
parcel id and reason (still well-formed XML), scale bar lengths, import list.

Screener suite: 311 tests OK (291 + 20). No live run.
