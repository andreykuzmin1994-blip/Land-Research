# 37 — Screener site planner, built check and viewer: decision note (Tier 2, Decide)

**Decision: approved and committed on `claude/magical-hypatia-htmio2`. Not
pushed, not merged.** The PR needs the operator's approval. After the merge,
a human runs
`python -m screener.autoresearch.evaluate --decide --new-baseline` on the
merge commit (R7). The score should not change, because the evaluator
imports only `screens` and none of the new modules.

Roles ran as fresh-context subagents in one workflow; model: same as the session (recorded by the orchestrator in the PR).

## What was verified (this phase re-ran everything; nothing taken on report)

| Gate (01 checklist) | Result |
|---|---|
| Protected files (R5, R7, SR-1) | `git diff --exit-code 2ec740c` is empty for the existing screener modules, `settings.toml`, both `requirements.txt` files and `autoresearch/`, and for the root `prepare.py`, `parameters.json`, `sources.json`, `program.md`, `zoning_rules.json`, `building_prototypes.json`, `rules_sources.json`, `STANDING_RISKS.md`, `CLAUDE.md` and `START_HERE.md`. The changed files are the six new modules and the template, 3 test files, 2 fixtures, README.md, SITE_PLANNING.md, AGENTS_DESIGN.md and reviews/37. |
| Suite offline (R8, SR-6) | `python -m unittest discover -s screener/tests -t .` with the proxy at 127.0.0.1:9 ran 898 tests in 96 s, OK. The cache had 2217 files before and after. The runtime is over budget (see the deviations). |
| No owner data (R1, SR-8) | The guard tests pass for every artifact type. No `startswith` on an owner field, no owner prefix, and no owner text in an outFields value. The shared `viewer.OwnerGuard` scanned 63 output files of this phase's replays (planner in both modes, the assemblage, the built check and the viewer page) and found **0 hits beyond the run's own public site addresses**. A separate exact-string scan against 4,860 owner and mailing strings matched only the public place name "Union City". |
| Not checked is never clean (R2, R9) | The tests pass. The replay also showed it on real data: the round-2 tile widening asked for layer tiles of #55 and #26 that were not in the cache. Offline, both read `LAYERS_NOT_CHECKED` with `feasible: null`, never clean, and the CLI exited 1 and named them. A live `--merge` run then re-planned only those two sites and exited 0. |
| Viewer safe (R3) | The adversarial tests pass. The CSP tag is present. All 52 replay SVGs parse with ElementTree and have no script, href or on* attribute. |
| Parity (R6) | See the next section. |
| Feasibility rule (R4) | The invariant holds on all 33 default records: 17 feasible, 15 not, 0 incomplete. `total_sf` is 0 unless the plan is feasible and not gated, and every infeasible record has a severity-1 reason. |
| Pond sizing (R5) | #4 gives 6.5 ac-ft in 1 pond and #33 gives 10.4 ac-ft in 2 ponds, in both modes. |
| Joins and paths (R10–R12), geometry (R13, R16–R18, R20, R21), politeness (R14, SR-11), draft labels (R15), R19, R22–R28 | The tests pass. `plan_checks` found 0 violations on 33 of 33 default records. Every record has `zoning.status == "draft"` and `ZONING_DRAFT`, and every SVG has DRAFT. The greps find no `arcgis.cache_days =`, `ThreadPoolExecutor`, PIL, numpy or `ATL_PARKING_VERIFY`. |

## Parity (offline cache replay, 2026-11-17, `--cache-days 30`)

Prototype mode (`--prototype-behaviour`) against `plans4.json`, 33 sites:
- Head fields and the flag set are identical on all 26 drawn single sites, with rank 25 under its `--cut-parcel`.
- The 6 NO_FIT records (#10, 19, 56, 60, 84, 110) now also carry `ZONING_DRAFT`, `feasible: false` and `total_sf: 0`, as R15 and R4 require.
- On all 26 SVGs, every drawn plan element left of the side panel is identical to `plan4-*.svg`. Only the side panel text changed.
- The assemblage had to be named `a115-170`, because a name made only of ranks must now list exactly those ranks' parcels (round 2). Its head and all 10 alternatives are identical. It took 847 s, and it adds `ASSEMBLAGE_MEMBER_NOT_RANKED` (severity 2).

Default mode against prototype mode (the behaviour fixes):
- **R4:**
  - #9 is gated (`USE_PERMIT_REQUIRED`), so `total_sf` goes from 254,000 to 0.
  - #44 is blocked by `HEIGHT_CAP`, so it is no longer feasible and `total_sf` goes from 189,000 to 0, with the same 189k building.
  - On #124, #170, #20, #41 and the assemblage, `POND_DOES_NOT_FIT` is promoted to severity 1.
- **R17, pond re-sizing:**
  - #5 drops from 115k to 109k.
  - #23 drops from 216k to 189k; its old pond sat on a route.
  - #26 drops from 102k to 84k.
  - #45 changes from operator 260 at 120k to 230 court + row at 124k.
  - #55 goes from 10.1 to 10.3 ac-ft.
  - Storage changes by 0.1 to 0.6 ac-ft on #9, 13, 48, 53, 57, 64 and 83.
- **Rescue pass (round 1):** #41 goes from 129k (`CAR_ROUTE_NONE`) to 61k, still infeasible.
- **Length-independent flags (round 2):** #20 now shows only `ACCESS_CONSTRAINED`, and its pond fits.
- **Score with the trailer penalty, after the round-1 and round-2 search changes:** #21 goes from 190-deep 68k (0 trailers, `TRAILER_SHORT`) to 160-deep 58k, which meets the trailer target.
- **Pieces (round 3):** the parcel shape of #100 is one polygon with a neck under 2 ft. It now reads as pieces of 11.1 and 3.5 ac. The pond must sit on the building's piece, so #100 drops from 162k to 68k. #4, #83 and #110 gain `PARCEL_MULTIPART` (severity 3) with no plan change.
- **New flags:**
  - `DISTRICT_BUFFER_NOT_DRAWN` on the Fairburn ranks.
  - `HSG_ASSUMED` on #3, 9, 13 and 57.
  - `SECOND_DROP_YARD_UNREACHABLE` on #53.
- **R21:** the assemblage's paving envelope goes from 46.4 to 45.6 ac.

Built check, offline, top 250, with `visual.json --visual-by-rank`, `--second-looks-from` the 2026-11-16 `land_today.json` and `--decisions`:
- Exit 0. land_today reads raw 112, partly 80, not raw 54, unclear 4. All 250 rows match on every field except `footprint_note` (170 rows, the new R9 wording) and one `what_is_there` (#250).
- builtcheck matches the reference except `why` (the same wording) and 5 county record addresses written as "(withheld)", because they read as an owner's mailing street.

Viewer, from the default replay, the built check, decisions and leads:
- 79 plans in 1,140,662 bytes, against 1,119,976 for the reference (R28 band met).
- The 6 no-fit sites are named on stderr.

## Phase-specific risks found and what changed

The risk review set 28 risks (R1 to R28) and the gates above. Three adversarial review rounds confirmed 29, 29 and 12 findings, all fixed or answered. These were the medium ones:
- **Round 1:**
  - The second-building budget wiped out a feasible plan.
  - The viewer showed 0 SF for gated plans.
  - siteplan had its own, weaker owner guard (now one shared guard).
  - Clean rectangles could read infeasible.
  - Grid snapping raised a false `ROUTE_OUTSIDE_ENVELOPE`.
  - A failed built-check input could still give raw_land.
  - The Fairburn district buffer was missing.
- **Round 2:**
  - `HEIGHT_CAP` and `ACCESS_CONSTRAINED` changed which layout was picked.
  - An assemblage used one member's rules.
  - The guard skipped owner strings found in any row's address.
  - Hollow county answers counted as checked.
  - An assemblage showed only its first member's land class.
  - `python -m` crashed on a bad rank.
  - Dual soil groups were read as D.
- **Round 3:**
  - The road/court seam cost 12 ft of truck area (#30 now matches the reference again).
  - A multi-piece parcel put its pond across a road.
  - `--merge` ignored a changed site definition.
  - An incomplete built check could read better than a complete one.
  - The viewer's "operator prototypes fit" filter counted every plan.

Correction to 01 R24: it called reading B/D as B "non-conservative". The code now reads a dual group as the letter that gives the larger storage. B/D reads as B, because D's coefficient gives about 27% less storage.

## Deviations and not fixed

- **Aerial download.** `aerials.py` is not ported, because it needs Pillow (R25, SR-10). The aerial review stays a manual step, and its only machine input is a schema-checked `visual.json`. The 2026-11-16 second looks live only in `land_today.json`, so a re-run needs `--second-looks-from`.
- **Test runtime.** R8 asked for about 45 s for the whole suite and 15 s for the new tests. The suite takes 96 s and the new tests take 72 s (237 tests). The behaviour tests run real fit searches; the slowest takes 6 s. Not trimmed further.
- **Per-site budget.** The default is 900 s, not R19's 120 s: #48 needs 193 s and the assemblage about 850 s.
- **Owner guard limits.** Misspellings, nicknames, run-together names and short individual names still pass (documented). The guard now exists once, in `viewer.py`.
- **Planner not fully monotone.** Floor area can dip on a larger parcel. Yield depends on the parcel's orientation (about ±30% on a test rectangle), which is documented.
- **Fixtures.** The two committed fixtures (rank 21 and rank 25) are trimmed real inputs with public parcel ids and site addresses and no owner fields. `test_siteplan` names the rank-25 cut parcel id once. That is a public id, not owner data.
- **Frontage cache age.** It follows `arcgis.cache_days`, not `--cache-days`, because `frontage.py` stays unchanged.
- **Research notes.** spec.md, critic.md and review_syn*.md stay in the session scratchpad. SITE_PLANNING.md summarises them.

## Operator decisions left open

These are listed in SITE_PLANNING.md § "Operator decisions (open)":
- The parking rule: `PARKING_RULE` max(code, market), `MARKET_PARKING_TIERS` 0.55/0.70/1.0, `--parking`.
- Atlanta parking: `ATLANTA_PARKING` market, with the `ATL_PARKING_CODE` flag and `--atlanta-parking`.
- The trailer target: `TRAILER_TARGET_TIERS` 0.5/1.0/1.3 per door, `--trailer-ratio`.
- The building prototypes: `PLANNER_PROTOTYPES` read only from `sitefit.PROTOTYPES`. `building_prototypes.json` is untouched. Market depths fill the 166k to 312k SF gap.
- Which draft zoning to verify first (`JUR_DRAFT`, all values draft).
- Paving in setbacks.
- Detention basis.
- The built-check thresholds.
- New from this phase: whether a pond may sit across a pinch or a road from the building (#100).
