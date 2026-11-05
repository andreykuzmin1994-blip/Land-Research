# 02 — Site fit: pre-change risk register and dispositions

> The register was written by an independent reviewer BEFORE the change
> (items SF-01 to SF-22, severity as given). Each item below carries what
> was done and the test that pins it. Standing risks (STANDING_RISKS.md):
> SR-1 extended to `building_prototypes.json` / `site_fit.py`; SR-2 scan
> extended to `site_fit.py`; SR-6 all new paths run on fakes; SR-7 the
> export goes through a slug + hash and `os.replace`; SR-8 the command
> prints no owners or addresses; SR-10 no new dependency
> (`requirements.txt` unchanged, pinned by a test); SR-14 no new
> `action_type`. Spatial overlay/buffer work is in PostGIS per Z-10
> (reviews/21_zoning), not SR-10, which is about dependencies (SF-22).

## SiteFit formulas cited (operator's SiteFit.html, not committed)

| What | SiteFit lines | Ported as |
|---|---|---|
| `DEF` prototypes, parking, detention, roads | 495-516 | `building_prototypes.json` |
| dock sides: cross [-1, 1], front [-1], rear [1] | 664 | `site_fit._sides`; export `p.type` |
| `e = min(endClr, L/2)`, `sp = max(doorSp, 8)` | 665 | `dock_doors` |
| `nT = floor(L / max(stall, 8))` per side, no end clearance | 666 | `trailer_stalls` |
| office pod `min(60, L/4, W/3)` on the front wall | 671 | schema: `endClr >= 60` |
| `n = span >= 0 ? floor(span/sp) + 1 : 0` per run | 718 | `dock_doors` |
| `depthTotal = W + sides * (court + trailer)` | 727 | `envelope_depth` |
| `parkRows` | 731-737 | `park_rows`, `lot_stalls` |
| `detVolume` | 760-764 | `detention_volume_cf` |
| building-only encroachment check | 2037 | stricter rule, SF-08 |
| `blankStudy` / `loadStudy` / file open | 546, 2367, 2415 | `study_document` |
| `mpd` / `toLocal` / `toLL`, `elLL` | 479-481, 943 | `to_local`, `to_ll` |

Hand-derived goldens (`SiteFitFormulaTests`): cross L 1,000: run
1,000 - 2 x 60 = 880, floor(880/12) + 1 = 74 per side, 148 docks;
floor(1,000/12) = 83 x 2 = 166 trailer stalls; depth 520 + 2 x 185 = 890;
520,000 SF. Front/rear L 640: run 520, 44 docks, 53 stalls, depth 445,
166,400 SF. Front L 400: run 280, 24 docks, 33 stalls, 104,000 SF. Cross
L 600: 41 x 2 = 82 docks, 50 x 2 = 100 stalls. `parkRows(124, 18, 24)`:
124 >= 60 (2 rows), 64 >= 60 (4 rows), 4 < 42, so 4 rows; car lot
500 x 124: 4 x floor(500/9) = 220. `parkRows(190, 55, 80)`: 2 rows x 50 =
100. Because `e <= L/2`, the run is never negative, so SiteFit's 0-door
branch is unreachable (a run of 0 has one door; pinned).

## Dispositions

| ID | Sev | Disposition |
|---|---|---|
| SF-01 | blocking | **Done.** One guard: research.py reads stored zoning only while `zoning.any_verified()` (unchanged), and asks for a projection only for a parcel passing the S9 gate; `_CycleCache.projection` is None unless something is verified. Nothing in research.py touches site_fit at import (the module's own load never raises). Tests: (1) frozen record of research.py at 28d3205 over 22 parcels (every class, null geometry, multipart, empty after buffer, 8.60 / 8.62 ac in four jurisdictions, 145,600 / 156,000 SF), checked by `tests/site_fit_golden_check.py` (results, every statement and parameter, inserts, notes, per parcel and cycle): identical; (2) `DraftDifferentialTests`: present vs missing vs malformed prototypes vs site_fit absent, zero site_fit statements, no new flag text, no cache field; (3) `tests/zoning_golden_check.py` exits 0; (4) live: 28d3205 vs new on the zoned landtest copy, all `parcel_scores` columns identical, metric 15 both (`03_decision_note.md`). |
| SF-02 | blocking | **Done.** One guarded branch in `_assess_strategy_bts`; ground lease keeps `_BTS_MIN_ACRES` for all parcels (comment + `test_bts_branch_is_one_guarded_switch`). `TruthTableTests` (verified/draft/unknown x 8.60/8.62 x 149,999/150,000/None, BTS and ground lease); `VerifiedBtsTests` (South Fulton verified, mixed cycle: other jurisdictions identical; only BTS moves against a verified-S9 run without projections). Decision note states the scope and that verifying a jurisdiction now moves S9 and BTS together. |
| SF-03 | blocking | **Done.** API `project_parcel(conn, parcel_id)` / `project_parcels(conn, ids)`, no tunables; frozen dataclass results; `BTS_MIN_FOOTPRINT_SF` and `meets_bts_footprint` (exact type check, verified label required) in the module. `StaticResearchTests`: (a) only the allowed `site_fit.*` names; (b) no assignment, AugAssign, `setattr`, `__setattr__`, `_replace`, `dataclasses.replace` on site_fit or result fields; (c) no read of any result field at all (stricter than "no arithmetic"); (d) no constant naming the prototypes file, no `PROTOTYPES_PATH`, `load_prototypes`, `_STATE`, `_FIT_CACHE`; the only 150,000 literal is the unchanged proxy. Monkeypatching stays SR-16 (decision note). |
| SF-04 | blocking | **Done.** Load once, schema (required keys, positive numbers, `endClr >= 60`, lattice, source text, ISO `as_of`; the repo's 2026-11-05 is pinned by a test rather than by the schema, so the human can re-date the file), deep freeze, SHA-256, `verify_prototypes_unchanged` (discovery, scoring, ad-hoc `score_parcel`), `verify_prototypes_committed` in `runner.evaluate` after the zoning check; suite stub in `tests/__init__.py` with the real function kept and `test_the_repo_file_is_committed`. Lists updated: CLAUDE.md, AUTORESEARCH_MECHANICS.md, STANDING_RISKS SR-1, START_HERE Step 1, agent_prompt.md, README, research.py docstring, discovery-fulton.yml paths, test_discovery (both forbidden lists + the whole-surface source/AST), test_mirror `_PIPELINE_MODULES`, `zoning_helpers.HUMAN_OWNED` (hashed by test_zz). `FreezeTests` (byte change, disappearing, appearing), `CycleEntryTests` (raise aborts both cycles before connect; ad-hoc scoring before any read), `test_docs_name_the_protected_files`. Autopilot allowlist unchanged (existing test). |
| SF-05 | blocking | **Done.** Containment is exact, not corner-based: the rectangle widened by 0.01 ft must contain no boundary edge of any ring (all parts and holes, clipped to the slab) and its centre must be inside (even-odd). Fixtures with exact answers: square, rectangle, rotated rectangle, U-shape, notch, two disjoint squares, square with hole, triangle, sliver, tiny, 324-vertex irregular; empty/split after buffering via the projection tests. Property test: 40 random polygons, every placement passes an independent densified-sampling check (winding number). Live: all 937 corpus envelopes `ST_Covers`-checked by PostGIS. |
| SF-06 | high | **Done.** `SRID_FEET = 2240`, the buffer distance a `%s` parameter, coordinates returned in 2240; US survey vs international foot (2 ppm) noted in the module. Live: 2240/geography area median 0.99983, all within 0.1%; buffer agreement within 0.5% (slivers excepted, stated); synthetic 1,000 ft square at 33.6 N buffered 25 ft = 950^2 within 0.2%. AST/regex test forbids 3857/900913. The offline 950^2 check is live only (offline has no buffer); offline tests pin the SQL text instead. |
| SF-07 | high | **Done.** Literal port; endClr >= office pod enforced by the schema instead of porting the run split; golden tests above. |
| SF-08 | high | **Done.** Full-envelope rule stated in the file, module and decision note; lattice in the file; schema rejects off-lattice minimums. Tests: 600 passes (156,000), 600.0 ft free gives 560 (tolerance) and 560 = 145,600 fails BTS; minimum above the fit reports "does not fit: geometry (needs ... envelope)"; capped at L; the binding constraint is named (full size / geometry / at minimum length / coverage cap). |
| SF-09 | high | **Done.** Rule as specified; result records value and source key ("setback 40 ft all sides = max(front/side/rear), south_fulton M-1, draft rules"). `RulesTests`: every listed district, nulls, default labels, same code in three jurisdictions, non-numeric reported. FID resolves to South Fulton's table through `zoning.district_for_stored` (same lookup as S9). |
| SF-10 | high | **Done (not modelled).** Constant "residential buffers not applied (neighbour zoning not in DB)" in every result, the memo, CLI and study notes; no adjacency SQL in site_fit (test); buffer keys never read (test: South Fulton M-1 = 40, Union City 50, Fairburn 35). The typed hook is the private exclusion function (SF-16). |
| SF-11 | high | **Done.** Coverage over gross geography area; the cap steps L down; FAR folded in as a cap (never binds one-story); "height not checked (clear 40/36 ft; district max ...)" whenever the district has a max height. Tests: 60% cap forces 560; no cap with the same geometry gives 640; coverage % matches the memo; Fairburn height note. |
| SF-12 | high | **Done.** Two depths shared by the three prototypes; 0-180 degree range; slab sweep over vertex events; fixed angle list; probe-line pruning (exact necessary condition) took the worst landtest parcel from 7.0 s to 0.15 s with identical corpus results. Projections only for verified parcels (scoring) and the top 10 (memo). Per-process cache keyed by the buffered geometry text, gross area, cap and both files' SHA-256. Offline timing test (324-vertex boundary, about 0.2 s, bound 2 s); live 1,075 parcels in 17.5 s; cache-vs-no-cache identity offline and live. |
| SF-13 | high | **Done.** PostGIS output rounded to 0.01 ft, parts sorted by (area desc, min vertex), local origin at the floored minimum, fixed angle list (edge angles rounded to 0.1 degree), first placement wins (angle asc, then y asc), 0.01 ft conservative tolerance. Tests: same input twice, parts reversed and vertices rotated give identical results; lattice boundary pinned; repr has no timestamp, uuid or geometry. Versions in the decision note. |
| SF-14 | high | **Done.** `ST_CollectionExtract(ST_MakeValid(...), 3)` as S2; each read in a SAVEPOINT when the caller is inside a transaction (rollback when idle); statuses ok / empty_after_setbacks / no_geometry / error / unavailable. Verified scoring: empty is N/A; no_geometry / error / unavailable is N/A plus one data_gap flag (`_flag_data_gap_once`); never the proxy. Tests: NULL geometry, empty, zoning/geometry read failure, injected failure inside a fake transaction then a successful next query (plus the control without savepoint), memo '—' with reasons. Live: "hole outside shell", NULL, empty, and an injected failing read on a real connection followed by a working query. The snapshot's read-only test is respected: no savepoint on a fake without transaction state. |
| SF-15 | should-fix | **Done.** "40% coverage of land per program.md: X SF; net of setbacks: Y SF"; max building SF from the largest fit; table per prototype; "used for BTS: yes (verified rules)" / "no (draft rules)". The program.md wording question is left to the human. Golden markdown (draft, verified, no geometry, empty), deterministic renders; existing snapshot/memo tests pass unchanged. |
| SF-16 | should-fix | **Done.** The flood/wetlands string is in every result, the snapshot, the memo line, the CLI output and the study notes (tests); the hook is private and returns `[]`; research.py passes only `(conn, ids)` (AST); no FEMA/NWI hosts in site_fit (test). |
| SF-17 | should-fix | **Done.** Study: deterministic id `lr-<sha256(parcel_id)[:12]>`, name = parcel id, open ring of the largest part, `setbacks` = applied value per edge, `edgeTags` blanks, `defaultSetback` 25, one building element (DEF fields, fitted L, building centre, rot in SiteFit's plane via the ported `mpd/toLocal`), `view` null, `notes` with the constraints and "largest part only" when multipart. Export re-fits inside the largest buildable part with 2 ft clearance. Offline round trip (SiteFit's rot/toLL applied to building and courts, inside the setback line; ring and setbacks lengths; dock side); live: exported buildings inside a geography buffer of the exported boundary. |
| SF-18 | should-fix | **Done, one deviation.** No owner/address selected or printed (sentinel test over stdout and the file; executed SQL has no owner/address); slug + hash file names (`../`, `/`, spaces); atomic write via reporting's helper into the gitignored `site_fit_studies/`; `SiteFit.html` and `sitefit/` gitignored (git check-ignore test). Deviation: no non-local-DSN refusal, because the operator's database is the hosted one; no DSN exits 2, the session is read-only and nothing shared is written (decision note, item 9). |
| SF-19 | should-fix | **Done.** site_fit imports only the standard library and `zoning` (not even prepare); research, reporting and cli import site_fit. SQL in module constants with `%s`. Tests: import allowlist, requirements unchanged, site_fit in the whole-surface SQL/AST scans, in the zoning and prototypes write scans, and in test_mirror's module list. |
| SF-20 | should-fix | **Done.** Docs (CLAUDE.md, AUTORESEARCH_MECHANICS.md, STANDING_RISKS SR-1) state immutable during a run and new run + baseline after any change once something is verified; runner order test (zoning check, then prototypes, then any work). Not done (optional): SHA in cycle notes, which would change notes in verified mode only; left for the human. |
| SF-21 | nit | **Done.** "car parking not modelled" on every projection; no stall count from leftover area (`parkRows` is ported and tested for later use only). |
| SF-22 | nit | **Done.** SiteFit.html not committed (gitignored); goldens hand-derived with line citations above; notes cite SR-10 (dependencies) and Z-10 (PostGIS) correctly; model-name grep over new and changed files is clean; both suites pass; the live databases are dropped; nothing pushed. |

## Notes for the post-change reviewer

- The 21_zoning regeneration recipe runs `python -c` from the repo root,
  where the empty `sys.path[0]` entry puts the CURRENT research.py first;
  this change's recipe runs from the directory holding the old file and
  asserts `not hasattr(research, "site_fit")`.
- `meets_bts_footprint` also requires the projection's own rules label to
  be verified, so a draft projection can never pass even if research.py
  were edited to ask for one.
