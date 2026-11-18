# Land screener

Finds 5–50 acre industrial-development land in Fulton County that looks
**buyable** (private, not built on, not owned by a developer, government,
utility or homeowners' / community association) and **buildable** (not
mostly floodplain, wetlands, protected land or steep ground). It then
ranks what's left: closeness to an interstate exit, the zoning path and
nearby industry raise the score, and so do signs of a motivated seller.
Exit distance and zoning shape the score but don't screen anything out
unless you switch on the optional screens in `settings.toml`. It writes a
one-page brief for each top parcel. Free public data only: no CoStar, no
paid parcel feeds, no scraping.

This is separate from the AutoResearch loop in the repo root (`research.py`,
`prepare.py`, `runner.py`), which is left untouched. It needs no database
and no secrets.

## Run it

```bash
pip install -r screener/requirements.txt     # requests + shapely
python -m screener.run                       # full Fulton run: about an hour the first time (measured 55 min, 2026-11-10), minutes after
python -m screener.run --limit 200           # quick trial
```

Results land in `screener/output/fulton-<date>/`:

| File | What it is |
|---|---|
| `leads.md` | **Start here.** The short reading list: grade A leads, then grade B, one table row each, with the caveats once at the foot (see Lead grade below) |
| `summary.md` | The funnel (how many parcels each screen removed), top 10, common reject reasons |
| `ranked.csv` | Every surviving parcel, best first, with the facts behind its score (plus a rough cut/fill estimate, a building-fit check, a detention allowance, SSURGO soils, GDOT road frontage and the lead grade that aren't scored; see below) |
| `map.html` | Open in a browser: survivors coloured by score, click for details |
| `briefs/` | One page per top parcel and per lead listed in `leads.md`: lead grade, site, owner, why it ranks, watch-outs, next steps, plus a `.svg` site plan beside each brief (same file name). A re-run into the same folder removes the brief and plan files the earlier run wrote (it keeps their names in `.briefs-written.json`) and nothing else in `briefs/` |
| `rejected.csv` | Every parcel screened out, with the reason |
| `ranked.geojson` | The same survivors for GIS tools |
| `features.json` | The evaluation's features for each ranked parcel, kept for your decisions (see Decisions below) |
| `facts.json` | The report facts each ranked parcel's row showed (fit, pond, soils, frontage, grade, score), kept for your decisions; never an evaluation input (see Decisions below) |

Map data is cached for 7 days and the county deed file for 30
(`screener/cache/`), so re-runs take a few minutes. `--refresh` forces
fresh downloads of both. The lidar slope answers are kept apart, in
`screener/cache/slope/`, for `[run] slope_cache_days` (3650 by default):
the survey is from 2018-19 and does not change, and the slope step is
most of an uncached run (about 49 of 55 minutes for Fulton, measured
2026-11-10). The service's answer for one shape is not as steady as the
survey (see "Public data changes between days"), so a kept answer is one
reading, held; `summary.md` says how many were reused and how old the
oldest is. `--refresh` leaves them alone; `--refresh-slope` asks the
lidar service again for every parcel, and falls back on the kept reading
for a parcel whose new request fails. A changed parcel shape or open
ground is a new request either way. The folder is small (about 2 KB per
parcel), so it can be copied to another machine to spare it the wait. If a map layer is down, or sends back a malformed
or partial answer, the run stops rather than treat its parcels as clear.
It also stops if the county parcels, deed file or industrial parcels can't
be read. Bad answers aren't cached, so a re-run asks again; if the stop
message mentions `--refresh`, re-run with it. A layer that has no
features near the parcels (which can be normal, e.g. no Opportunity Zone
nearby) doesn't stop the run, but `summary.md` names it.
If a city zoning service is down, the run continues and `summary.md` says
which zoning check failed. It also counts parcels whose lidar slope check
failed: they stay in the list, flagged, without the steep-ground screen.

A parcel whose county map polygon leaves less than one 10 m lidar pixel
(100 m²) of open ground is a map problem, not a failed check: the lidar
service answers such a shape with nothing every time, so it isn't asked.
Such a parcel stays ranked, because the tax record says there is land and a
hard screen belongs in `screens.py` (with a new evaluation baseline), but
it can't read as clean. It gets two flags: the parcel map vs tax-record
acreage mismatch (a 0.0-acre map shape included), and one saying that
slope wasn't checked and the map checks can't be trusted. Its 0%
floodplain and wetlands come from the tiny shape, so they mean nothing.
`summary.md` counts these parcels in their own note, which doesn't say
"re-run later". It does so with `--no-slope` too. A thin sliver bigger
than one pixel can still get an empty answer, and that still counts as a
failed slope check.

**Earthwork (rough cut/fill).** `ranked.csv` (and `ranked.geojson`)
carry two columns right after `relief_ft`, and each brief has an
"Earthwork" line:

- `earthwork_cy`: cubic yards of cut, balanced by the same volume of fill,
  to level the parcel's open ground to one flat pad at its mean elevation.
  Rounded to 2 significant figures, and to the nearest whole cubic yard
  below 100 (so `0` means at most half a cubic yard).
- `mean_cut_fill_ft`: the average depth of cut or fill over that ground, to
  0.1 ft. On a large, near-flat parcel it can read `0.0` while
  `earthwork_cy` reads a few cubic yards: a few hundredths of an inch
  spread over many acres.

They come from the lidar elevation histogram the slope check already
fetches (no extra request): cut = fill = area × mean absolute deviation
from the mean elevation ÷ 2. This is a rough grading-difficulty signal,
not a grading plan. It ignores the drainage cross-slope a pad needs,
leveling only the building or yard footprint, terracing (a single pad
grossly overstates large or ridged parcels), shrink/swell and compaction,
topsoil stripping, rock and unsuitable soils, and grading done since the
2018-19 lidar. There is no dollar figure, and it is in no score, screen,
flag or "why it ranks here" line. An empty cell (never 0, which means
flat to within half a cubic yard) means not computed: slope not checked (`--no-slope`, `slope =
false`), the slope check failed, the open ground is under one lidar pixel,
or the answer didn't support an estimate (an unreadable histogram, or a
pixel count that doesn't fit the open-ground area: under half or over
twice it, e.g. a large NoData gap). A number that is shown covers the
whole open ground, but not every pixel of it was seen: a smaller gap (up
to half the open ground) is filled by assuming the missing ground is like
the rest, so the estimate can miss a pit or mound hidden in the gap. The
elevation statistics were identical across repeated requests (below), so
this number is steadier run to run than `steep_pct`.

The frozen evaluation set has no elevation data, so the estimate is
attached to each parcel only after scoring, flags and the sort, and
`rank.py` and `scoring.py` never see it (`tests/test_earthwork.py` spies on
every call). The AutoResearch loop's own `evaluate.py` doesn't run that
test: it holds because the loop edits only `rank.py` and can't reorder
`run.py`, and every `exp:` diff gets a human read.

**Site fit (largest open part and a building envelope).** `ranked.csv`
(and `ranked.geojson`) carry three columns right after `mean_cut_fill_ft`,
and each brief has a "Building fit" line:

- `largest_open_acres`: map acres of the largest contiguous part of the
  open ground, i.e. the parcel minus floodplain (FEMA SFHA, floodway
  included), mapped wetlands and water (NWI), conservation easements
  (NCED) and protected land (PAD-US). `0.0` means no open ground is left
  on the parcel map (the constraints cover it, or what is left rounds to
  under 0.05 acres). Empty means not computed.
- `envelope_fit`: the largest building envelope (building + truck court +
  trailer parking per dock side) found on that open ground inside a 25 ft
  lot-line setback: `cross-dock L x 520 ft` (lengths 600-1000 ft, 890 ft
  deep envelope) or `front/rear-load L x 260 ft` (lengths 400-640 ft, 445
  ft deep; front and rear share a depth, so they are one check). Lengths
  step by 40 ft. A cross-dock fit means the front/rear envelope fits too.
  `none found` means it was checked and nothing fit at the minimum length;
  `not checked: <why>` means it wasn't (`[fit] enabled = false`, outside
  `[fit] max_parcels`, a shape over 600 vertices after a 1 ft simplify, or
  a geometry fault; each is counted in `summary.md`).
- `envelope_building_sf`: the fitted building's square feet, empty unless
  something fits.

The prototypes and the rotated-rectangle search are copied (not imported)
from the repo root's `building_prototypes.json` and `site_fit.py`;
`tests/test_sitefit.py` fails if the root file changes and the copy
doesn't. The search is a lower bound (a 2 degree angle grid plus edge
directions), so `none found` is not proof that nothing fits. Limits:
**steep ground is not located** (the slope check gives only a share, so
steep ground may sit inside the open area); utility, pipeline and access
easements are not mapped; car parking, drives, detention, frontage and
access, coverage caps and zoning setbacks beyond the 25 ft are not
modelled. When the parcel map disagrees with the tax record the fit uses
the map shape and the brief says so. Most parcels take well under a
second, but the measured worst case is about 4 to 5 seconds per parcel: open
ground just under the 600-vertex cap (for example about 100 small wetland or
flood pockets) where nothing fits, so every angle is tried. `[fit]
max_parcels` caps it to the top N by rank. Like earthwork, it is computed only after scoring, flags
and the sort, and is in no score, screen or flag. To make it a learned
feature or a screen, a human would add it to `build_eval.FEATURES`,
rebuild `data/` and take `--new-baseline`; reading it from `rank.py` is
not the way.

**Detention pond (rough allowance).** Two more columns follow
`envelope_building_sf`, and each brief has a "Detention pond" line:

- `detention_pct`: the operator's prototype detention pond (400 x 200 ft
  at the top of bank, 6 ft deep, 4:1 side slopes, about 9.1 acre-feet,
  1.84 acres) as a percent of the parcel's open ground on the county map
  (all parts). Empty when there is no open ground, or when detention is
  off or outside `[detention] max_parcels`; still filled when only the
  placement is not checked (for example the building fit is off).
- `pond_fit`: `pond fits` when that rectangle was placed (copied
  rotated-rectangle search) on the open ground left beside the fitted
  building envelope, inside the 25 ft lot-line setback; `pond fits (no
  building envelope placed)` when no envelope fit, so it was tried on the
  open ground alone; `no pond room found` when it was checked and no
  placement was found (a lower bound: the envelope is placed first at its
  largest length, so a shorter building may leave room); `not checked:
  <why>` when it wasn't (`[detention] enabled = false`, outside
  `[detention] max_parcels`, the building fit not checked, a shape over the
  vertex cap, or a geometry fault; each is counted in `summary.md`).

The pond numbers and the volume formula are copied (not imported) from
the repo root's `building_prototypes.json` ("detention") and `site_fit.py`;
`tests/test_detention.py` fails if the root changes and the copy doesn't.
This is a rough allowance for one fixed pond, **not a stormwater design**:
no hydrology, no NOAA Atlas 14 rainfall, no runoff from the impervious
area and no local detention rules. The pond may be placed right against
the envelope with no room left for access, grading or a buffer. The real
requirement may be larger,
smaller, underground or shared. Steep ground is not located. It calls no
web service, is computed after scoring, flags, the sort and the building
fit (with or without `--no-slope`), and is in no score, screen or flag.
When placed, the pond is drawn in teal on the site plan. Most parcels
take a few milliseconds; the measured worst case (the same near-cap shape
as the building fit, where nothing fits) is about 3 to 4 seconds more per
parcel, and `[detention] max_parcels` caps it to the top N by rank.

**Soils (USDA SSURGO).** Four more columns follow `pond_fit`, and each
brief has a "Soils" line. After the sort, the fit and the pond, the
screener sends one request per ranked parcel to USDA NRCS Soil Data
Access (`post.rest`, `sdmdataaccess.sc.egov.usda.gov`): the SSURGO map
units intersecting the parcel shape (WKT in lon/lat order), clipped to it
by SDA, with each unit's clipped area and its `muaggatt` aggregate ratings.
Shares are of the parcel's mapped soil area, by map unit:

- `soil_hsg`: the hydrologic soil group (A, B, C, D or dual A/D, B/D,
  C/D, "D unless drained") covering the most area, dominant condition.
  Under urban land or water this can be a minority of the parcel: the
  brief then says "largest rated group" instead of "mostly" and gives the
  unrated share.
- `soil_hydric_pct`: area-weighted share of hydric components.
- `soil_poorly_drained_pct`: share mapped poorly or very poorly drained.
- `soil_dwellings_limited_pct`: share rated "Very limited" for dwellings
  without basements (NRCS's slab-on-grade building rating, the closest one
  the aggregate table carries; "small commercial buildings" is per
  component and is not asked for).

Three states stay apart: a value; `none rated` when no unit on the parcel
carries that rating (urban land, water); and `not checked: <why>` in
`soil_hsg`, with the three share columns empty, when the check didn't run
or failed (`[soils]` off or outside `max_parcels`, an HTTP error, SDA's
empty `{}` or a header-only answer, a malformed row, an answer at SDA's
100,000-row cap, or mapped soil covering under half or over 1.5 times the
parcel shape). Bad answers are never cached. After 5 service failures in a
row the rest of the run isn't asked (SDA is down nightly about
00:00-02:00 Mountain); `summary.md` counts every reason. SSURGO is mapped
at about 1:12,000 to 1:24,000: small wet spots, fill and inclusions go
unmapped, and it is no geotechnical report or wetland delineation. Soils
are in no score, screen or flag (`tests/test_soils.py` spies on every
call). Access: SDA's robots.txt is absent (HTTP 404, checked 2026-11-10)
and NRCS publishes `post.rest` as a public web service for ad hoc queries;
the screener sends its own User-Agent, at most 3 requests at a time
(`[soils] workers`, 1-3) and caches answers like every other source.
`[soils] max_parcels` (250 by default) caps it to the top N by rank.

**Road frontage (GDOT road centrelines).** Four more columns follow
`soil_dwellings_limited_pct`, and each brief has a "Road frontage" line.
After the sort, the fit, the pond and the soils, the screener sends one
`identify` request per ranked parcel to GDOT's public Functional Class map
service (`maps.itos.uga.edu/arcgis/rest/services/GDOT/GDOT_FunctionalClass`,
GDOT's statewide road network as published by GDOT with UGA ITOS): every
GDOT centreline in the parcel's box padded about 500 ft, on the seven
functional-class layers (interstate, freeway/expressway, principal and
minor arterial, major and minor collector, local road). The parcel line
within 50 ft of a centreline counts as frontage on that road:

- `frontage_ft`: parcel line within 50 ft of any public road that is not
  access controlled and not private.
- `frontage_road`: class and route of the road with the longest frontage
  ("minor arterial, SR 14"). The layer has no street names, so a county or
  city road shows its GDOT route id; the county's road type stays in the
  brief's Access line.
- `frontage_limited_ft`: parcel line within 50 ft of an interstate or
  freeway/expressway, or of a ramp, collector-distributor road, ramp
  connector, managed lane or Y-connector on any layer: access is
  controlled and a driveway unlikely, so it is kept apart.
- `frontage_private_ft`: parcel line within 50 ft of a road GDOT marks as
  private: mapped, but not public access (check an access easement).

The road's type is the first character of its GDOT route id (GDOT's
FUNCTION_TYPE domain: 1 main line, 2 ramp, 3 collector distributor, 4
ramp-CD connector, 5 frontage road, 6 alley, 7 managed facility, 8
Y-connector, 9 private, P projected, Z other, R roundabout). Projected
alignments (not built) are left out, and only a main line is labelled
"SR N"; a ramp or other piece of a state route shows its GDOT route id.

Three states stay apart: a number and a road; `0` with `none within 50 ft`
(the brief gives the nearest road's distance and says "possibly
land-locked"); and `not checked: <why>` in `frontage_road`, with all three
numbers empty, when the check didn't run or failed (`[frontage]` off or
outside `max_parcels`, an HTTP error, an answer without a results list, a
road from another layer, without polyline geometry or not in WGS84, or an
answer at the service's 1,000-result cap). Bad answers are never cached;
after 5 service failures in a row the rest of the run isn't asked, and
`summary.md` counts every reason. It is a screening measure: up to about
50 ft at each end of a frontage can be a side line near the corner, a wide
right-of-way or a misaligned parcel map can hide real frontage, and being
near a road is not legal access. **A curb cut / driveway permit is not
checked**: the brief's frontage line and its next steps say to confirm it
with GDOT (state routes) or the city or county (local roads). The site
plan draws the frontage in orange. The roads are not a map layer
(`layers.LAYERS`) and not in the map checks, so nothing in the
AutoResearch evaluation copies them; frontage is in no score, screen or
flag (`tests/test_frontage.py` spies on every call). Access: the host's
robots.txt is absent (HTTP 404, checked 2026-11-10), the service is public
with no sign-in and no posted terms forbid automated use; the screener
sends its own User-Agent, at most 3 requests at a time (`[frontage]
workers`, 1-3) and caches answers like every other source. `[frontage]
max_parcels` (250 by default) caps it to the top N by rank.

**Lead grade and `leads.md`.** Three more columns follow
`frontage_private_ft`, each brief opens with a "Lead grade" block, and
`leads.md` lists the leads by grade (`grade.py`, `digest.py`,
`reviews/35_screener_lead_layer`):

- `lead_grade`: `A`, `B`, `C`, `blocked`, or `not graded: <why>`.
- `lead_found`: what the checks found against the parcel, in words.
- `lead_not_checked`: the core checks that could not be read.

The grade is one fixed rule over the facts above. It is **not a score and
not a screen**: `ranked.csv` keeps the order the score gave it, and no
parcel is removed. Every check reads clear, found or not checked, and
**only a state the check itself reports as good is clear**: a failed
service, a check that was off, a missing result or an unknown state is
"not checked", never clear.

| Check | Clear | Found | Not checked |
|---|---|---|---|
| Zoning path | `by_right`, `likely` | `possible`; `hard` blocks | `unknown` |
| Building fit | an envelope fits | `none found` | fit faulted or missing |
| Detention pond (read only when an envelope fits) | `pond fits` | `no pond room found` | not checked |
| Road frontage | at least `min_frontage_ft` (50) of public, not access-controlled frontage | less, or none within 50 ft | the road service failed |
| Slope | a lidar reading came back (the steep-ground screen was applied) | | the reading failed, or the open ground is under one lidar pixel |
| Soils | an answer came back under both thresholds, or with no rating | hydric or poorly-drained share at or above `max_hydric_pct` / `max_poorly_drained_pct` (25; 0 = off) | the soil service failed |
| Parcel map against the tax record | they agree | | an acreage mismatch (every map-based check may be off) |
| Same owner built next door (`owner_next_door`) | no adjacent same-owner parcel is built on | one is: adjacent directly or through other same-owner parcels, in the run's 5-50 ac groups or, with `next_door_any_size`, among touching parcels of any size | adjacency could not be measured, or the any-size check failed or was outside `next_door_max_parcels` |

Watch items count only when found (the fact is on the tax roll or it is
not): a listed contamination site on the parcel, or within
`contamination_near_ft` (250; 0 = off), or on a bordering parcel (any
size, `next_door_any_size`); an owner name that reads like a quarry or
aggregates company or a waste or sanitation company (`operating_owner`,
`owners.operating_kind`); the assessor's "land-locked" entry (when
frontage did not already count); and a last priced deed more recent than
`recent_purchase_years` (5; 0 = off). Contamination counts once, the
closest fact first.

**Neighbours of any size (`nextdoor.py`, added 2026-11-13 after the lead
document checks).** The run fetches only 5-50 acre parcels, so a quarry
pit or a 94-acre distribution centre next to a lead is not in its
same-owner groups. For the top `next_door_max_parcels` (250) by rank, the
screener asks the county parcel layer for the parcels touching each one
(within the owner-cluster gap, 10 ft), whatever their size, and follows
the same owner's parcels outward up to three steps (`owners.same_owner`,
the loose name rule). A same-owner parcel counts as built by the screen's
own test (improvements over `[screens] max_improvement_value` and over
half the land value), unless its county land-use code is residential (1xx: a family's own house is not an
operating site). A listed site (EPA, GA EPD HSI) whose mapped point falls
on a touching parcel, but not on the lead itself, is the "bordering
parcel" item. Both add a flag line. About one request pair per lead,
cached with the map data; a failure is "not checked" and counted in
`summary.md`. Limits: an operator holding its plant under another
company name is not followed; a site mapped at a street address off the
bordering parcel is missed; nothing here reads deeds or leases.

`blocked` if the zoning path is `hard` (that rests on no other check).
Otherwise a parcel outside a check's `max_parcels` cap, or run with a
check switched off (`--no-slope` included), reads `not graded: <why>` and
still lists what was found. Otherwise the number of found and not-checked
items decides: none is **A**, one is **B**, two or more is **C**.

Limits:

- "Found" is what the screen found, with every limit of the check behind
  it. "No building envelope found" is a lower bound for the copied
  prototypes (the smallest needs a clear 400 x 445 ft), not proof nothing
  can be built; frontage is measured to a road centreline; soils are
  map-unit level.
- The "built next door" item sees the same-owner parcels the run fetched
  (5-50 acres, exact owner name and mailing address; see Owner clusters)
  and, with `next_door_any_size`, touching parcels of any size under a
  loosely matching name. A plant under another company name is not seen,
  so "clear" here is still weak. Its flag lines ("Same owner, adjacent and
  built on", "Same owner, any size") stay in the watch-outs even with
  `owner_next_door = false`.
- The recent-deed item reads the county deed file. Any priced deed
  counts, so a transfer between related companies reads the same as a
  purchase. That a recent buyer is an unlikely seller is a rule of thumb,
  not something this tool has tested.
- A answers "did every check come back clear", not "is this a good
  site". Nothing here knows whether a parcel is for sale, who is behind a
  company, or what the utilities can serve.

`leads.md` lists A leads in rank order, then B, at most `[grade]
digest_max` (40), and each listed lead gets a brief even outside the top
`briefs`. C, blocked and ungraded parcels are counted, not listed. County
text in the table cannot add a column or carry markup (pipes, line
breaks, brackets, angle brackets, backticks, asterisks and underscores
are replaced). `[grade] enabled = false` writes `not graded` everywhere and
an empty list.

Like the facts it reads, the grade is attached only after scoring, the
sort and every report check; `rank.py`, `scoring.py` and `screens.py`
never see it (`tests/test_grade.py` spies on every call), and nothing in
`autoresearch/` imports it.

**Site plan (`briefs/*.svg`).** Each brief links a plain SVG drawn from
the county parcel map in the same flat local plane as the fit, north up,
with a scale bar: the parcel outline, the constrained ground (floodplain,
mapped wetlands and water, conservation easements, protected land), the
largest open part and the fitted envelope with its prototype and SF. Steep
ground is not shown. Without a map shape the brief says "Not drawn"; a
parcel whose map checks or fit didn't run gets the outline with "not
checked". The file holds no owner, mailing or site address. It is drawn by
`report.write_all` after scoring and adds nothing to `ranked.csv` or any
score (`tests/test_plan.py`).

**Public data changes between days.** On 2026-10-28 and 29, the USGS 3DEP
lidar service returned different slope statistics for 464 of 2,110
identical requests (about 22%), while the elevation statistics were
identical. Overnight, changed Fulton appraisal values moved about 19
parcels between "already built on" and ranked. Parcels near a threshold
can therefore flip from one run to the next: `max_steep_pct` (share of open
ground steeper than 15%), `min_usable_acres` and `max_improvement_value`.
Comparing two runs then mixes data changes with code changes. To compare
code, run both versions on the same cache (no `--refresh`, within
`cache_days`), after checking that every source you compare was cached:
map layers and county parcels are kept for 7 days, the deed file for 30,
and slope answers for `slope_cache_days` (so `steep_pct`, and the
steep-ground screen with it, no longer change from run to run for an
unchanged parcel; `--refresh-slope` takes a fresh answer, which can
differ).

**Decisions and the feature snapshot.** Each run writes
`features.json` next to `ranked.csv`: for every ranked parcel, its
`parcel_id`, `lon` / `lat` (the evaluation's spatial blocks need them),
every field in the evaluation's feature list (`build_eval.FEATURES`,
imported, so the two can't drift) under `features`, and the fields the
evaluation blanks (`build_eval.BANNED_AS_NONE`: zoning, utilities, slope,
jurisdiction and so on) under `live_only`. Its `meta` names the run date
and time, county, `--limit` (a trial run), whether slope was checked, the
settings file's SHA-256, the code commit (null if git doesn't answer), the
record count, and how many NaN or infinite values were written as `null`
(`summary.md` says so too). It holds no geometry, owner name, mailing
address, earthwork estimate or decision, and it is copied before scoring,
so it is what the parcel looked like going in. A re-run into the same
folder deletes the old `features.json` before it rewrites the reports, so a
run that fails part-way never leaves one run's features next to another
run's `ranked.csv`. Rejected parcels are left
out: their map checks are partial. `python -m screener.decide` copies a
parcel's record, with the run's `meta`, into
`screener/inputs/decision_features.jsonl` when you decide, so the features
outlive the run folder.

Each run also writes `facts.json`: for every ranked parcel, the report
facts its row showed (`facts.COLUMNS`: rank and scores, zoning path,
slope, earthwork, building fit, pond, soils, frontage, the lead grade,
owner type and holding period), with the same run `meta`. It holds no
geometry, owner name, mailing address, site address, flags or decision.
`python -m screener.decide` copies a parcel's record into
`screener/inputs/decision_facts.jsonl`, a separate file from
`decision_features.jsonl`, so a later look at your marks can ask what
was on screen when you made them (did grade A leads get pursued more
often than C?). A run without `facts.json` (an older run folder) still
records the decision and says no facts were kept. **These facts are never
evaluation features**: the frozen evaluation did not measure them,
nothing in `autoresearch/` reads either file, and Phase B (below) is
built from `features.json` alone.

Some names mean slightly different things live and in the evaluation:
`usable_after_slope` is slope-adjusted live but equals `usable_acres` in
the evaluation; `rail_ft` / `rail_owner` come from the whole live rail
layer but only main lines and yards in the evaluation; ownership fields
are as of today live and as of 1 January of each as-of year in the evaluation.

Neither a decision nor the snapshot is on a parcel while it is scored
(`tests/test_decisions.py` spies on `rank.learned_score` and
`scoring.fixed_score`). Turning decisions into evaluation labels (Phase B)
is a human `--new-baseline` step and has not been done: it must handle
selection bias (only parcels an earlier ranking put near the top get
reviewed), the meaning differences above, spatial clustering and privacy
(the label file stays out of git and holds no notes).

## Tune it

- `screener/settings.toml`: acreage, which cities to search, and every
  screen threshold (floodplain %, wetlands %, slope, usable acres, which
  owners to exclude). Optional screens: `max_exit_miles` and
  `exclude_hard_entitlement` (drop land zoned and planned residential).
  `exclude_association_owned` drops land held by homeowners', property
  owners', community, neighborhood, town, townhome, condominium or civic
  associations: subdivision common areas, not land for sale. The owner
  name must pair an owners' word with an association word ("... HOMEOWNERS
  ASSN INC"), or carry HOA inside a company name with a legal form ("... HOA
  INC"; THE doesn't count, since Hoa and The are also Vietnamese given and
  middle names); ASSOC, ASSOCIATION or POA alone don't count.
  `built_min_building_sf` (2500) and `built_min_improvement_value`
  (50000) drop a parcel the county's deed file lists with that many square
  feet of commercial buildings and at least that much in improvements: an
  operating site, even when its improvements are a small share of the
  land value (a fuel terminal, a rental-car facility). `0` square feet
  switches it off. It reads the current roll, so it is a live-only screen:
  the frozen evaluation never applies it (its answer-key sites are built on
  now). Norfolk Southern's real-estate company (SOUTHERN REGION INDUSTRIAL
  REALTY) counts as railroad-owned (`exclude_utility_owned`). Unlike the
  building screen, that owner rule is shared with `build_eval`: the frozen
  key is unchanged, but a rebuild of it would drop that owner's parcels
  from the candidate pool too.
  `[owners] adjacency_gap_ft` sets how close two same-owner parcels must be
  to count as adjacent (see Owner clusters below; flags only).
  `[fit] enabled` and `max_parcels` switch the building-fit check off or
  limit it to the top N ranked parcels (see Site fit; report only).
  `[detention] enabled` and `max_parcels` do the same for the detention
  pond allowance (see Detention pond; report only).
  `[soils] enabled`, `max_parcels` and `workers` (1-3) do the same for the
  SSURGO soils check (see Soils; report only, one web request per parcel).
  `[frontage] enabled`, `max_parcels` and `workers` (1-3) do the same for
  the GDOT road frontage check (see Road frontage; report only, one web
  request per parcel).
  `[grade]` holds the lead grade's switch, its thresholds
  (`min_frontage_ft`, `max_hydric_pct`, `max_poorly_drained_pct`,
  `owner_next_door`, `recent_purchase_years`, `contamination_near_ft`,
  `operating_owner`) and `digest_max`, the most leads `leads.md` lists
  (see Lead grade; report only). The grade itself asks no service;
  `next_door_any_size` and `next_door_max_parcels` switch on and cap the
  neighbour check it reads (`nextdoor.py`, one request pair per parcel,
  run whether or not grading is on).
  `[run] slope_cache_days` is how long a lidar slope answer is reused.
- `screener/inputs/decisions.csv`: mark parcels `pass`, `listed`,
  `pursue` or `maybe` (by hand or with `python -m screener.decide`).
  Passed and listed parcels drop out of future runs; `maybe` is a mark
  only. See `inputs/README.md` and Decisions below.
- The score has two parts:
  - **`screener/scoring.py`, 35%, set by you:** zoning / future land use
    path, slope, utilities and Opportunity Zone. The answer key can't
    measure these, so AutoResearch never touches them.
  - **`screener/rank.py`, 65%, tuned by AutoResearch:** access to an
    interstate exit, usable acres, nearby industry, rail, and seller
    motivation (holding period, inherited, out-of-state, owner type).

## How it works

```
parcels (county GIS, 5-50 ac, chosen cities)
  -> ownership & land-only screens   (assessor record)
  -> deed history, built-site screen (county CAMA extract: commercial buildings)
  -> map checks                      (flood, wetlands, easements, protected land,
                                      contamination, exits, rail, Opportunity Zones,
                                      nearby industrial parcels)
  -> zoning + future land use        (each city's own GIS)
  -> map screens, then lidar slope   (USGS 3DEP)
  -> rank -> report
```

## Data sources (all free; tested live 2026-10-27)

| Check | Source |
|---|---|
| Parcels, owner, mailing address, values | Fulton County PropertyMapViewer, layer 11 |
| Deed history, road type, water/sewer | Fulton County CAMA extract (`Tax_CAMAInfo.zip`, refreshed yearly) |
| Zoning / future land use | South Fulton, Fairburn, Union City, East Point, Atlanta, Fulton Industrial District GIS |
| Floodplain, floodway | FEMA NFHL (Esri Living Atlas copy) |
| Wetlands, ponds | USFWS National Wetlands Inventory |
| Conservation easements, protected land | NCED, USGS PAD-US |
| Slope, relief, rough cut/fill | USGS 3DEP lidar (2018–19), resampled to 10 m |
| Contamination | EPA Cleanups in My Community; Georgia EPD Hazardous Site Inventory |
| Interstate exits | GDOT |
| Freight rail | BTS North American Rail Network |
| Opportunity Zones | HUD |
| Soils (report only) | USDA NRCS SSURGO via Soil Data Access |
| Road frontage (report only) | GDOT Functional Class road centrelines (maps.itos.uga.edu) |

## What it can't tell you (check these by hand)

- **Utilities.** Atlanta, Cobb, Clayton and Henry don't publish water/sewer
  maps. The assessor's water/sewer field is a weak hint. Call the utility.
- **Zoning in College Park, Hapeville, Palmetto and Chattahoochee Hills.**
  We found no machine-readable zoning there, so the brief says "unknown".
- **Wetlands.** The national inventory dates from the 1980s and misses
  small and forested wetlands. A field delineation is still needed.
- **Easements other than conservation easements.** Power-line, pipeline,
  sewer and access easements are not in any free layer. Read the title.
- **Whether it's for sale.** We found no free listings feed that permits
  automated use (LoopNet and Crexi terms prohibit scraping). Mark listed
  parcels in `decisions.csv`. This tool does not use CoStar data. CoStar's
  license terms on exports, databases and AI use are for the firm to
  confirm with counsel against its signed agreement (see also
  `COSTAR_INGESTION_CONTRACT.md` in the repo root).
- **Owner contacts.** Mailing addresses only. LLC officers need a manual
  Georgia Secretary of State lookup.
- Distances are straight-line to the nearest exit with local access
  (freeway-to-freeway interchanges are skipped), not drive time.

### Owner clusters

Each ranked parcel's watch-outs say when the exact same tax-roll owner
name AND mailing address is on other parcels in the run
(`screener/owner_clusters.py`), and which of them touch it or lie within
`[owners] adjacency_gap_ft` (10 ft by default) on the county parcel map,
directly or in a chain. A chain of two or more that adds up to the
`[area]` acreage band is flagged as a possible assemblage; a bigger one as
"larger than the search band". Every sibling is listed (up to five) with
its status in this run: ranked, screened out and why, or not screened in
a `--limit` trial. Limits:

- **Only the run's band and area.** The groups are built from the parcels
  the run fetched (5-50 ac in the chosen jurisdictions), before any screen
  and before `--limit`. The owner may hold more elsewhere, and small
  neighbours under 5 ac that would make a site with this one are not seen
  (a wider fetch is a later phase, with its own review).
- **Exact match only.** Names are matched after upper-casing, turning
  commas and periods into spaces, collapsing runs of spaces and joining
  spaced legal forms ("L.L.C." = "LLC"). Nothing else is normalised:
  hyphens, apostrophes, "&" and "#" are kept with their spacing, so
  "SMITH-JONES" and "SMITH JONES", "O'NEAL" and "ONEAL", "A&B" and
  "A & B", or "#200" and "# 200" do NOT join. Addresses get the same
  treatment, with a ZIP+4 (hyphen, space or no separator) cut to five
  digits. Legal forms, numbers and suites are kept, so "FUND I" and "FUND
  II", or suite 100 and suite 200, stay apart. Different spellings,
  addresses or per-site LLCs of one owner are NOT joined. A blank or
  placeholder name, or a mailing address with no numbered street line,
  joins nothing. These are not the looser deed-matching rules in
  `owners.py`.
- **The gap.** 10 ft bridges digitizing slivers, not a road right-of-way
  (40-80 ft): parcels across a road are not "adjacent". A shared corner
  point counts as touching, which is not usable frontage; check the shared
  boundary on the map. Acres are the tax record's.
- **It cannot widen the developer exclusion.** The owner type comes from
  the name alone, so a recognised developer's other parcels are already
  screened out under the same name. What the groups add is the siblings
  screened out for other reasons (built on, exempt, passed, steep).
- **Not a score or a screen.** The groups are built in a local variable and
  added to the flags only after scoring and the sort; `rank.py` and
  `scoring.py` never see them (tests pin this). The evaluation has no such
  fact. Putting portfolio size or assemblage acres in the score would be a
  human evaluation step: build the same exact-key groups on each as-of
  year's snapshot in `build_eval.py` (owner and mailing address exist there), add
  a FEATURES entry, and rebuild with `--new-baseline`. Do not read the
  flag text from `rank.py`.

## Checking a lead against documents

After a run, two agents can look into one graded lead: an investigator
reads the public documents (ordinances, staff reports, plans, county GIS),
then a skeptic looks for the strongest reason the lead fails. Their rules
are in `INVESTIGATE.md`. Every claim they make rests on an exact quote and
its address, and `quotecheck.py` fetches each address itself and keeps a
claim only if the quote is on the page:

```bash
python -m screener.quotecheck screener/leads/<parcel>/findings.json
python -m screener.quotecheck screener/leads/<parcel>/findings.json --with-skeptic --findings-sha256 <hash the first run printed>
```

It writes `findings.checked.json` and `lead.md` in the lead's folder
(`screener/leads/` is kept out of git; the data branch keeps the results).
`lead.md` is written by the code: words in double quotation marks are a
kept quote and a link is a kept claim's address. PDFs need the `pdftotext`
command (poppler-utils).

- **What a kept claim proves:** the quoted words were on that page when
  the tool fetched it. Not that the page is current, nor that the claim
  follows from the quote. A skeptic verdict changes no grade or rank.
- **What it will not fetch:** anything but public http(s) pages by GET;
  an address that names CoStar, LoopNet or Crexi anywhere; a page its
  host's robots.txt disallows, or whose robots.txt can't be read; a
  redirect to another host.
- **Known limits** (`reviews/35_screener_lead_layer`, 06 to 08): a query
  page can show a request's own words back, and the guard against that is
  not airtight (the skeptic compares each quote with its address); text
  hidden by a style sheet and invisible PDF text count as page text; the
  zoning code (Municode) and the Secretary of State cannot be read by a
  polite fetch, so those two stay a person's job.

## Site plans and the built check

Three tools work on a finished run folder. They are optional and run by
hand; `python -m screener.run` never calls them, and they change no
score, grade or rank. All three use one owner guard (`OwnerGuard` in
`viewer.py`) on the final text of every file they write, and refuse to
write when it finds an owner name or owner mailing address of the run
(`ranked.csv` and `rejected.csv`). The guard matches the tax-roll strings
whole-word, after decoding entities and JSON escapes, ignoring case and
reading any run of spaces and punctuation as one break between words,
with the usual street spellings read alike (`RD`/`Road`, `STE`/`Suite`,
`N`/`North` and the like, and `&`/`and`), a company suffix spelled in
letters read as the word (`L.L.C.`, `L L C` and `LLC`) and an apostrophe
after a one-letter prefix dropped (`O'Brien` and `OBRIEN`): the full
name, the name without company suffixes (8 characters or more, counted
as written), an individual's surname and first name in either order (8
characters or more; middle initials and a trailing `ET AL` or `JR` are
dropped first, surname particles such as `DE LA` stay with the surname,
and for a couple, `WALKER DONALD & BETTY`, each person's name and
"Donald and Betty Walker"),
the full mailing address, its street line and the street line without
its suite or unit ("P.O. Box" and "PO BOX" read the same), and each
drawing's text with its markup removed. It skips strings of 6 characters
or fewer, one-word generic names ("cemetery"), a string that is part of
the same parcel's own site address (an owner-occupied house), a string
that is part of a jurisdiction name of the run (a city's own land), a
one-word string that is a word of any site address of the run (a street
word such as "hillside"), and, in each file, a string that is part of a
site address that file itself writes (its sites' own published
addresses). Another parcel's site address never makes a name or mailing
line pass. It cannot catch a misspelt name or a nickname. No
request these tools write asks for a parcel owner or mailing field (the
map layers keep `layers.py`'s field lists, which name the easement
holder and a protected area's manager, public bodies; only the shapes
are used).

- `screener.builtcheck` asks whether a top parcel is really land today.
- `screener.siteplan` draws a screening test fit of an industrial
  building on chosen parcels.
- `screener.viewer` puts the run's briefs, the site plans and the built
  check into one HTML page.

The design basis, the numbers and the operator's open decisions are in
[`SITE_PLANNING.md`](SITE_PLANNING.md).

### The built check

```bash
python -m screener.builtcheck RUN_DIR --out screener/output/builtcheck-<date> \
    --visual visual.json --decisions screener/inputs/decisions.csv    # --top 250 is the default
```

The screen drops a parcel whose own tax record shows a building. It can't
see a building on a separate tax record, a paved yard, outdoor storage, a
quarry or a landfill. For the top N parcels by rank this reads three
signals:

1. FEMA USA Structures footprints inside the parcel's outline, with holes
   in the county shape filled in. A building in a hole is often on a
   separate improvements-only record.
2. The holes in the county parcel shape.
3. Other Fulton tax records that lie mostly inside the outline, with their
   class and improvement value.

| File | What it is |
|---|---|
| `builtcheck.csv` / `.json` | One row per parcel. `built_check` is `built`, `check`, `no building found` or `not checked`, with the reason and the measurements. The JSON adds the records found inside the outline and the thresholds used. |
| `land_today.csv` / `.json` | One row per parcel. `land_today` is `raw_land`, `partly_usable`, `not_raw_land` or `unclear`, with what the photo shows and where the verdict came from. |

**The aerial review is a manual step.** `land_today` rests on a look at
the county's 2024 aerial photo of each parcel. The screener has no image
tooling (its dependency rule allows only `requests` and `shapely`), so
reviewers who can see the images, a person or an agent with image
access, write the verdicts into a `visual.json` file. Its format is in
the `builtcheck.py` docstring. The rule that turns the file into
`land_today`:

- Where the photo and the footprints agree, the photo's verdict stands.
- Where they disagree, or the reviewer was unsure, a second reviewer
  looks again with both in hand. That verdict goes in the file's
  `second_look` part. Until then the parcel reads `unclear`.
- No photo review, a footprint check that did not complete, or a check
  with any input missing (a failed footprint or records fetch, even when
  the other input found something), never gives `raw_land`. The failure
  is named in the `land_today` row's `footprint_note`. A hollow answer
  counts as a failed input: county records not in WGS84, a county answer
  without the parcel's own record, or a footprint without a shape.

A `visual.json` that names no run and no parcel ids (the 2026-11-16 one)
needs `--visual-by-rank`, which says you vouch it was made for this run.
That file holds only the first looks. The 71 second-look verdicts of the
2026-11-16 check are rows of its `land_today.json` (source `second
look`); without them those parcels read `unclear` (73 unclear instead of
4). Carry them over with `--second-looks-from`:

```bash
python -m screener.builtcheck RUN_DIR --out screener/output/builtcheck-<date> \
    --visual screener/output/builtcheck-2026-11-16/visual.json --visual-by-rank \
    --second-looks-from screener/output/builtcheck-2026-11-16/land_today.json
```

Each carried row is joined by rank and parcel id; a file that names
another run is refused, and so is a file with no second-look rows (for
example `builtcheck.json` given by mistake). A `second_look` entry in
`visual.json` itself wins over a carried one. A second look whose
`raw_land` was held back because an input of that check failed reads
`unclear` with source `second look (held: an input failed)`; it is not
carried (the tool says how many were skipped), so carry second looks
from the file they were first recorded in, as above.

Without a second look, a parcel whose check did not complete (`not
checked`, or an input missing) keeps only a `not_raw_land` first look;
a first look of `raw_land` or `partly_usable` reads `unclear`, so a
failed fetch never reads better than a completed check. The viewer adds
"built check incomplete" to such a parcel's land text.

Exit codes: 0 done; 1 written, but some parcels read `not checked` or
were checked with an input missing (a fetch failed; run it again later);
2 refused, nothing written (owner text found, a foreign run, a
missing or unreadable input such as `ranked.csv`, `--visual` or
`--decisions`, a `--second-looks-from` file with no second looks,
`--top` below 1, or a `--cache-days` that is not a number of 0 or more). It asks
`services2.arcgis.com` (USA Structures) and `gismaps.fultoncountyga.gov`,
at least a second apart per host, through the shared cache
(`--cache-days`, 7 by default). 250 parcels take about 10 minutes cold.

What it can't tell you: the footprints come from imagery of 2012 to 2022,
so newer buildings and recent demolitions are missed. "No building found"
means no mapped footprint, not empty ground. The photo review is a
person's or an agent's judgement of one image, not a site visit.

### Site plans

```bash
python -m screener.siteplan RUN_DIR --ranks 4,33 --out screener/output/siteplans-<date>
python -m screener.siteplan RUN_DIR --assemble "north=<parcel id>,<parcel id>" \
    --out screener/output/siteplans-<date> --merge
```

For each site the planner draws one explicit industrial cross-section
(rear-load or cross-dock) on the open ground inside draft setbacks and
buffers. It reports the building, dock doors, trailer and car stalls,
truck and car routes from two driveways on the frontage, detention ponds
sized from the impervious area, a drop-trailer yard, a second building
where the land allows, grading, and a list of flags. The search tries the
operator's prototypes and market depths and keeps the best feasible plan.

Each plan then gets a read-out (`siteplan_assess.py`), printed on the
sheet and kept in the record: one status, the next steps in order, where
every acre goes, the plan measured against its draft zoning values, the
layouts compared, a takeoff with no prices, and each flag as a condition
with a workstream, a stage, an action and what closes it. The read-out
is derived from the finished plan and changes no layout, flag,
feasibility or floor area.

- `--assemble NAME=PID,PID` plans several parcels as one site. The ids
  are typed out; nothing picks parcels by owner. At most 12 ids. A name
  made only of ranks (`2`, `115+170`) must list exactly those ranks'
  parcels. The whole site is planned under the draft rules of its
  best-ranked member's jurisdiction. When members fall under other draft
  rules the plan is flagged `ASSEMBLAGE_MIXED_RULES` at severity 1 (drawn,
  not counted: which code governs is for a person to say); a different
  zoning in one jurisdiction is the same flag at severity 2, and a member
  not in `ranked.csv` (its rules unknown) is `ASSEMBLAGE_MEMBER_NOT_RANKED`.
  Every member's entitlement gate applies (an East Point I-1/I-2 member
  gates the site), and the pond uses the members' soil group that gives
  the larger pond.
- `--cut-parcel RANK=PID:FT` removes a parcel plus a buffer from a site,
  as a test cut (a cemetery, a house you would not buy). One cut per
  site: the option given twice for the same site is refused.
- `--merge` keeps the plans already in `--out`, when they come from the
  same code (the planner files and every screener module they import,
  directly or not: `sitefit.py`, `geo.py`, `plan.py`, `viewer.py`,
  `frontage.py`, `checks.py`, `layers.py`, `arcgis.py` and the rest), run
  and options (the prototypes' dimensions included). `--redo` plans those
  sites again. A kept plan of a named site whose `--assemble` members or
  `--cut-parcel` differ from this call's is dropped and the site planned
  again (named on stderr). A kept plan that is incomplete (an input not
  checked) is planned again when the call names it; one the call does
  not name keeps the exit code at 1 and is listed on stderr. A name that
  would take a kept plan of another site's key (`a+b` then `a-b`) is
  refused.
- `--parking`, `--atlanta-parking` and `--trailer-ratio` are the
  operator's open decisions (`SITE_PLANNING.md`). Every plan records the
  values it used.
- `--underground-detention` is for a market where land is dear enough
  for a vault (the operator's decision: not Atlanta). It changes what
  the read-out says when the surface pond holds the building. The layout
  still draws surface ponds only.
- `--max-seconds-per-site` is the search budget, 900 s by default (above
  0, up to 86,400; anything else is refused). Most
  sites take under a minute; a 78-acre assemblage took 14 minutes. A
  site cut short is flagged `PLAN_BUDGET_EXCEEDED` and counts as not
  feasible, in both behaviours. When only the optional second-building
  search is cut, building 1 stands and the plan says
  `SECOND_BUILDING_NOT_SEARCHED`; when only the extra search for smaller
  layouts (below) is cut, the plan says `SMALLER_LAYOUTS_NOT_ALL_TRIED`.
- `--prototype-behaviour` reproduces the session's v4 prototype's
  numbers. It is there for the parity check in
  `reviews/37_screener_site_planner/`; the draft labels, input states and
  budget rule are the same in both behaviours, and the default carries
  that review's fixes.

| File | What it is |
|---|---|
| `plans.json` | One record per site: the configuration, `feasible`, floor area, doors, trailers, cars, coverage, detention, access, grading, flags by severity, `gated` (a use permit), the alternatives tried, the draft zoning values used, the operator decisions used, and the state of each input; then the read-out (`assessment`, `site_data`, `compliance`, `quantities`, `selection`). The header names the run (`ranked.csv`'s SHA-256) and the code. |
| `plan-<site>.svg` | The test-fit sheet: the labelled plan, key numbers, next steps, where the land goes, the draft zoning check, the layouts compared and the conditions. |
| `plans.csv` | The planned sites side by side, one row each: status, counted and drawn floor area, yield, detention, grading, a larger layout that passed (if any), the layout that passed all but the pond (if any), the blocking flags and the first step. Parcel ids and numbers only, no address. |

How to read the sheet:

- **Status** (top right; `assessment.status`). `fits` (only the checks
  every site gets remain), `fits_with_conditions` (the site has its own
  severity-2 flags), `gated` (a use permit), `hypothetical` (no road
  frontage), `blocked` (not feasible), `incomplete` (an input was not
  read, or the search was cut short with a plan drawn: run it again),
  `no_fit`, `not_planned` (the search ran out of time before any plan).
  It restates `feasible`, `gated` and `hypothetical`; it is not a second
  verdict. A condition "blocks the plan" only when the record itself
  says the plan is not feasible, so the read-out agrees with the record
  under `--prototype-behaviour` too.
- **Next steps, in order.** What blocks the plan first, then the rest by
  stage: desk checks, before an offer, due diligence. The cheap checks
  come first, since a desk check that kills the site saves the survey.
  The actions are the usual next step for each flag (`PLAYBOOK` in
  `siteplan_assess.py`). None of them was checked against a document.
- **Where the land goes.** Gross acres, what the draft setbacks and the
  mapped constraints take, the net paving envelope, each use, and what
  is left unused inside the envelope. The lines add up to the gross.
- **Plan against the draft zoning values.** The building and the paving
  measured to each kind of lot line, the height, coverage, parking and
  use, with the margin on each. A setback is measured off the lot line
  itself, not past its ends (the planner's envelope is square at the
  ends of a line too).
  "Meets" says the drawing honours the draft value. It is not a check
  against the adopted code. One row is building code, not zoning: the
  open yard between the building and the nearest lot line that is not a
  road (60 ft under the unlimited-area rule, 40 ft with rated walls).
  How the draft values compare with the codes read so far is in
  `reviews/38_site_plan_read_out/02_draft_offsets_check.md`.
- **Layouts compared.** The plan drawn first, then the others tried,
  with why the plan ranks first. The ranking gives no floor area for
  trailer stalls (the operator's decision), so when the plan drawn is
  short of its trailer target the sheet names the largest layout that
  meets it. When a layout passed every layout check but the surface
  pond, it says so; underground detention is not offered in this market.
- **Takeoff.** Paving by duty, earthwork for one level pad (with the pad
  elevation and the deepest cut and fill), disturbed area. No prices.
- **Context on the plan** (since 2026-11-17). The neighbouring county
  parcels in outline, residential ones tinted by the same land-use test
  as the residential edges, and contour lines of the existing ground,
  traced from the planner's lidar samples (a 40 ft grid, smoothed; the
  interval and the index contours are in the legend). Both are drawn
  from inputs the planner already reads and change nothing in the
  record. The contours are indicative, not a topographic survey.

How to read a plan:

- **`feasible`** is `true` only when no severity-1 flag stands other than
  an entitlement gate (those are listed in `gated`): there is a truck
  route and a car route, the pond fits, and the draft caps hold.
  `false` means something kills or cuts it. `null` means an input could
  not be read (a flag ending in `_NOT_CHECKED` or `DEM_PARTIAL` says
  which); it is not a clean result, so run it again. One exception: when
  no building fits at all (`NO_FIT`), `feasible` stays `false` even with
  an input missing, because a missing input can only make the envelope
  larger.
- **`hypothetical`** is `true` when the frontage was checked and the
  parcel has no usable frontage (`ACCESS_NONE`): the plan shows what
  would fit if access were bought. It is `null` when access is unknown
  (the frontage check failed), never a landlocked signal from an outage.
- **`gated`** lists entitlement gates such as `USE_PERMIT_REQUIRED`. The
  geometry can be feasible while the use still needs a permit; the
  floor area is then not counted in `total_sf` (it stays in
  `total_sf_if_access`).
- **`alternatives`** lists the layouts tried. Their `geometry_ok` says
  whether a layout passed its own checks (routes, pond, plan checks, the
  coverage cap); it is not the site's feasibility, which also needs
  access, complete inputs and the draft height cap. A flag no other
  layout of the site can clear (`HEIGHT_CAP`, `ACCESS_CONSTRAINED`)
  blocks the site but leaves the layouts alone: the plan drawn, the
  second building and `total_sf_if_access` are those the site would have
  without it.
- When the pond or a route blocks every plan the search tried first, it
  also tries shorter lengths and the shallower layouts it left out, so a
  larger parcel does not read worse than a smaller one inside it.
- **Drainage** (since 2026-11-18, reviews/41; default behaviour only).
  A pond on ground more than 3 ft above the building pad cannot fill by
  gravity: the plan says `POND_UPHILL` (from 1 ft, `POND_ABOVE_PAD`, for
  information: a deeper pond takes more room than is drawn), and a layout whose pond drains
  ranks before a larger one whose pond does not. The pass below starts
  pond first: the lowest pond spots are reserved and every cross-section
  is fitted round them. The sheet prints the pad elevation and how far
  the pond's ground is below (or above) the pad;
  `detention.ponds_over_pad_ft` (each pond), `pond_over_pad_ft` and
  `drains_by_gravity` (empty where nothing was measured) are in
  `plans.json`. One level pad only: no paving grades, pipes or outfall. Reference site #25 went from a 67,000 SF plan whose pond
  stood 9 ft above the pad to 70,000 SF with the pond 24 ft below it.
- After the search, a pass takes the layouts the pond alone blocked and
  tries them with other pond proportions and moved to leave the pond
  room (since 2026-11-17, reviews/40; default behaviour only). It only
  adds layouts: the plan drawn is the best of all by the usual ranking.
  It refuses a layout that turns its docks to the street or its
  loading to homes where the plan before the pass did not, or that has
  no more floor area and fewer trailer stalls. It has 90 tries and half
  the remaining time, keeps what it
  found when cut, and is skipped when less than half the site's budget
  is left (time limits: a site near them can plan differently on a
  slower machine; `inputs.pond_room` says so).
  Reference site #21 went from 68,000 to 91,000 SF. A plan it adds may
  have fewer trailer stalls (the ranking gives them no floor area).
  `inputs.pond_room` in `plans.json` says what it did.
- **The planner does not check whether a parcel is built.** Run the built
  check first and plan only parcels it reads as `raw_land` or
  `partly_usable`; the viewer shows both side by side.
- Severity 2 flags need checking by a person; severity 3 flags are
  information.

**These are test fits, not engineering.** They come from public maps:
county parcel lines, USGS lidar, the national wetland and flood layers,
HIFLD transmission lines and GDOT roads. They are not a survey, a civil
design or a zoning determination. Detention is good to about ±30%, and so
is the building size: the layout search depends on how the parcel sits on
the map (`SITE_PLANNING.md`, "Still out of scope").
Routes, fire access and grading are indicative.

**Zoning values are DRAFT.** Every setback, buffer, parking code,
coverage cap and height cap is a draft screening value (`JUR_DRAFT` in
`siteplan.py`, with `JUR_TIGHTENED` laid over it since 2026-11-17: the
values that read looser than the codes, tightened at the operator's
request, and a 60 ft open yard at the building), mostly from code
mirrors, not checked against the adopted codes. Every record carries `zoning.status: draft` and the
`ZONING_DRAFT` flag, and every drawing says DRAFT. A jurisdiction with no
draft rules gets South Fulton's, flagged `JURISDICTION_DEFAULTED`. The
root system's human-owned `zoning_rules.json` is the authority, and
nothing here reads or changes it.

What a plan can't tell you: stream buffers, petroleum pipelines, utility
capacity, fire flow, sight distance, turn lanes, traffic, truck swept
paths, retaining walls, outfalls, tree rules and cost are not modelled.
Residential edges come from county land-use codes, not zoning. The search
can miss a layout that straddles a corridor. `SITE_PLANNING.md` has the
full list.

Exit codes: 0 done; 1 written, but some site failed (a bad id, a county
lookup that failed) or a written plan is incomplete (an input fetch
failed, so `feasible` is null or a `_NOT_CHECKED` flag stands; run it
again later); 2 refused before planning (bad arguments, an unreadable
run folder, or `--out` in the run folder or in a tracked part of the
repository); 3 owner text found in an output: the run stops, and no file
of that site is written (its drawing and `plans.json` are both checked
before either is written; sites finished earlier in the call stay). It
asks the Fulton parcel service, HIFLD (`services1.arcgis.com`), USGS 3DEP
elevation (`elevation.nationalmap.gov`), GDOT roads and the map layers in
`layers.py`, through the shared cache. Its own county, HIFLD and
elevation requests are spaced a second apart per host and stop after
five failures in a row (a young cached answer is still used after
that). `--cache-days` sets the county and HIFLD cache lifetime; elevation
answers are kept for 10 years whatever it says (lidar does not change).
The GDOT frontage and the map layers go through the existing screener
code and keep its cache and retries.

### The viewer

```bash
python -m screener.viewer RUN_DIR --out screener/output/site-plan-viewer-<date>.html \
    --plans screener/output/siteplans-<date> \
    --land screener/output/builtcheck-<date>/land_today.json \
    --decisions screener/inputs/decisions.csv --leads screener/leads
```

One self-contained HTML page: the run's briefs and the site plans, with
filters by decision, building fit, land today and plan feasibility, and
sorts by rank, floor area, SF per gross acre and acres. For a site plan
the panel beside the drawing shows the planner's read-out: the status
and headline, the next steps, the key numbers, what holds the yield,
where the land goes, the draft zoning check, the layouts compared and the
conditions (the flags as recorded are one click below). A plan made
before the read-out existed shows its numbers and flags as before. It
reads no network.

- Every input is joined by parcel id, and an input that names its run
  must name this one. A plans file with no run fingerprint (the
  prototype's `plans4.json`) needs `--join-by-rank`.
- From `decisions.csv` it reads only the decision value, and whether the
  note says "probably taken" (shown as the status). The note text
  appears only with `--include-notes`. From the lead folders it reads
  only the skeptic's verdict; the one-line headline appears only with
  `--include-headlines`. Both are still owner-checked.
- Every SVG is parsed and rewritten from an allow-list (no scripts, links
  or styles), and the page allows only its own script to run.
- A planned site with no drawing is not on the page. The viewer names
  those sites on stderr in two lists: no building fits, and not planned
  (the search budget ran out, or an input was not checked, before any
  plan was made: re-run the planner).
- A plan of several parcels shows its worst member: the land class
  (not raw land, unclear, not reviewed, partly usable, raw land, in that
  order) and the status (pass before pursue), with each member's class
  in the "Land today" text.
- A plan's floor area reads as counted, or, when it is not, says why
  (gated by a use permit, an input not checked, no road access, or a
  severity-1 flag).
- Exit codes: 0 written; 2 refused, nothing written (owner text found, a
  foreign run, an unsafe SVG or file name, a plan SVG named in
  `plans.json` but missing, a missing or unreadable input file, a
  `--leads` path that is not a folder, or a page over 15 MB).

### After merging these tools

They add files under `screener/`, so `evaluate.py --decide` refuses
until a human records a new baseline on the merge commit:
`python -m screener.autoresearch.evaluate --decide --new-baseline`. The
score is expected to be unchanged: the evaluator reads none of the new
modules. The same holds for any later change to them (the read-out and
sheet of 2026-11-17, `reviews/38_site_plan_read_out/`).

## AutoResearch

`screener/autoresearch/` runs Karpathy's keep-or-revert loop on `rank.py`
against an answer key it can't game: Fulton land that its owner sold,
before any building, to someone who had a $1M+ industrial building on it
by 2025, asked from each January of 2015 to 2019. That's 45 distinct
sites (147 site-years), spatially split 35 dev / 10 test. See
`screener/autoresearch/PROGRAM.md`.

Key rebuilt 2026-11-12 (`reviews/36_screener_eval_cohorts_metric`, 05 to
13): five as-of years instead of 2016 alone, a sale must precede the
building, a parcel bought at a real price since 2015 counts only under
the owner before, and a change is judged on a graded score (where each
site ranks) instead of whole top-2% hits. The hand-built ranking scores
graded 11.7 of 35 dev sites (2.4 in the top 2%); random ranking averages
3.1. Earlier scores are not comparable.

```bash
python -m screener.autoresearch.build_eval          # rebuild the frozen key (humans only, ~5 min; needs data/test_blocks.json)
python -m screener.autoresearch.evaluate --decide --new-baseline   # humans: the first baseline, and after any change under screener/ but rank.py
python -m screener.autoresearch.evaluate --decide   # the loop: once per experiment
python -m screener.autoresearch.evaluate            # a look at the dev score (logged)
python -m screener.autoresearch.evaluate --test     # sealed test split (humans only; logged)
python -m screener.autoresearch.evaluate --null-test               # humans: how often the keep rule keeps noise (about 20 minutes on 4 cores)
```

`experiments.tsv` and `best_ranking.json` are not in git, so a new cloud
session has neither: the loop then stops ("no baseline") until a human
records one. Keep them with the branch the loop runs on if a batch must
span sessions.

To run the loop, open a fresh Claude Code session and say: "Read
screener/autoresearch/PROGRAM.md and run 15 experiments."

The evaluator refuses (prints `REFUSED: ...`, exits 3, shows no score,
logs nothing) when:

- `rank.py` breaks the purity rules (below). Its source is checked before
  it is run, in every mode but `--control` (which does not run it), so a
  refused file never runs and nothing is scored.
- a file at the repository root, or a package folder there, is named
  like a standard module (`random.py`, `csv.pyc`; a `venv/` folder is
  fine), or a module was loaded from inside the
  repository but outside `screener/`: it would run inside the evaluator
  unseen by the git checks.
- `rank.learned_score` gives a record a different score the second time
  it is asked. Every record of every split that is scored is asked again,
  in an order drawn fresh each run, by a fresh copy of `rank.py` in a new
  Python process: a score may depend only on the record. (If that second
  process cannot start, nothing is scored and the message says so.)
- `experiments.tsv` was written by an earlier version of the evaluator
  (other columns): a human moves the old file aside. A look is refused
  too, because it could not be logged.
- (`--decide`) there are uncommitted changes or untracked files under
  `screener/` (ignored files such as `cache/` don't count), the experiment
  commit touches any file besides `screener/rank.py`, git can't be read,
  or this commit was already scored. Fix the commit and run it again.
- (`--decide`) any score is NaN or infinite, or outside 0–100. Fix
  `rank.py`.

and, where a human then records a baseline with `--decide --new-baseline`
(at the best kept commit when only its record was lost):

- (`--decide`) there is no baseline for this key in `experiments.tsv`
  (a first run, a new container, a deleted log, a rebuilt key). Only
  `--new-baseline` records one; the loop never runs it.
- (`--decide`) any file under `screener/` other than `rank.py` changed
  since the best kept run (the evaluator, `screens.py`, `data/`, a
  package `__init__.py`; documents and `screener/tests/` aside), or the
  best kept run's record is broken: `best_ranking.json` is missing,
  unreadable, from another run or does not give its logged score, or
  `experiments.tsv` has kept runs, none of which is in this branch's
  history. Deleting one of those files can't reset the bar.
- (`--decide`) the key in `data/` was built before the 2026-11-11 builder
  rules: a human rebuilds it first.

A new baseline starts a new chain: later experiments are compared with
it, not with older runs. It prints the best kept run it replaces and
whether `rank.py` differs from it. (`--decide --test` and
`--new-baseline` without `--decide` are usage errors: exit 2.)

A decision prints the two scores to one decimal and their difference,
never the bootstrap win share (that goes to the log only): finer numbers
would let one experiment ask whether a band of parcels holds a known site.
Every scoring that runs `rank.py` and shows a number is logged as a
`look`, `test` or `null` row with a hash of `rank.py`. After a DISCARD
the evaluator prints the `git reset --keep` that takes back discarded
experiments and nothing else (`--keep`, not `--hard`: it stops rather than
delete uncommitted work anywhere in the checkout); when a reset would remove any other commit
it prints "Do not reset" and no command.

The `rank.py` that is checked is the one that runs: its bytes are compiled
by the evaluator, not loaded through Python's import cache (which once
scored an older `rank.py` of the same size, edited within the same
second; the live `python -m screener.run` still imports it the ordinary
way). Git is asked about the repository the evaluator is in, whatever the
working directory.

### The purity check

`rank_problems` in `evaluate.py` reads `rank.py` as the importer does: the
file's bytes, decoded by their own coding declaration. That declaration
must be UTF-8: any other `# coding:` line is refused, because under
`utf-7` a comment can decode to a line of code. The check refuses:

- imports other than `__future__`, `math` and `typing`. From `typing`, only
  annotation names (`Any`, `Optional`, `Union`, `Mapping`, `Sequence`,
  `Iterable`, `Callable`, `Dict`, `List`, `Tuple`, `Final`, `Literal`,
  `TYPE_CHECKING`); no module objects or private names through any of
  them;
- the names `open`, `eval`, `exec`, `compile`, `getattr`, `setattr`,
  `delattr`, `globals`, `locals`, `vars`, `breakpoint`, `input`, `print`,
  `help`, `exit`, `quit`, `type`, `format` (and `str.format`),
  `get_type_hints`, `ForwardRef`, `hash` and `id` (those two differ from
  run to run); dunder names (`__x__`); private (`_x`) attributes;
- attributes of frames, generators, coroutines, tracebacks and code
  objects (any attribute starting with `f_`, `gi_`, `cr_`, `ag_`, `tb_` or
  `co_`), and keyword arguments named `co_...` or `__x__`;
- `match` statements (use if/elif), a function or class with a dunder name
  (`__init__`, `__getattr__`, `__instancecheck__`...), and class keywords
  such as `metaclass=`;
- binding a dunder name with `import ... as` or `except ... as`
  (`import typing as __builtins__` would make every later function look
  up bare names such as `sys` in `typing`'s namespace);
- assigning to or deleting an attribute of an imported module or name
  (`math.isfinite = ...`), and using a module as a value (`m = math`);
- `global` and `nonlocal`. (Other state kept between calls, such as a
  mutable default or a module-level list, is not seen by this check; the
  two-pass scoring catches it when it changes a score.)
- strings (docstrings aside) that name the evaluation files, or that are
  exactly a dunder (`"__globals__"`), a frame or code attribute (`"f_back"`)
  or one of the refused builtins (`"open"`).

These rules close the routes known on Python 3.11–3.13 to read an
attribute named by a string (`match` class patterns, rebuilt code objects,
metaclass hooks, `str.format`, a swapped `__builtins__`). They also close
the known routes to the evaluator's stack frames, which hold the answer
key, and to `open`, which reaches the key files on disk. Known routes are
closed; others may exist.
The check is a drift guard, not a sandbox. A `rank.py` written to cheat
could still find a route nobody has listed, so a human reads every `exp:`
diff, as with `research.py` (`STANDING_RISKS.md` SR-16). Scoring in a
separate process wouldn't help: every route to the frames found so far
also reaches `open` and the files in `data/`. Real isolation needs
filesystem confinement, which the standard library can't give portably.

Known gaps:
- A dunder or frame attribute name can still be computed as a string
  (`"_" * 2 + ...`). The rules refuse the known ways to use such a string
  (getattr, `match`, `type()`, class hooks, code objects), not the string.
- A planted `.pyc` for another module the evaluator imports (not
  `rank.py`, whose checked bytes are compiled directly) would still run;
  git ignores `__pycache__`, so `--decide` doesn't see it.
- State that gives the same score in every process (a table keyed by a
  record's own values) is not state the second pass can see; rule 3 and
  the human read of each kept diff stand against lookups.
- Changing shared objects through their methods (such as registering a
  class with an ABC) isn't detected.
- The check for root-level files that shadow a standard module is best
  effort. It runs inside the evaluator's own process, after its imports:
  a shadow that does load (an untracked root `csv.py` or `csv.pyc`,
  against rule 1) can switch the check off before it looks
  (`reviews/36_screener_eval_cohorts_metric/10_fourth_round_check.md`,
  finding 3). Run from a clean checkout.
- The reset hint trusts `experiments.tsv`: a hand-added discard row that
  names a human's commit gets that commit reset away.
- The check for repository folders on `sys.path` also runs after the
  evaluator's own imports, so a planted module that removes its own entry
  is not seen, and a folder named `site-packages`, or one under the
  running virtual environment, is exempt
  (`reviews/36_screener_eval_cohorts_metric/15`). Both need someone who
  sets `PYTHONPATH` or plants files; the loop's command does neither.
- The live `python -m screener.run` doesn't run the check.
- Python 3.14 adds public evaluation hooks (`annotationlib`,
  `ForwardRef.evaluate`, template strings) that these rules haven't been
  reviewed against.

## Tests

```bash
python -m unittest discover -s screener/tests -t .
```

Run it from the repository root with `-t .`: without it `screener/tests`
lands on `sys.path`, and the evaluator's tests (rightly) see a repository
folder there.

## Adding a county

Copy `fulton.py` to `<county>.py` with the same functions
(`fetch_parcels`, `load_cama`, `fetch_industrial_points`,
`ZONING_SOURCES`) and set `county = "<county>"` in `settings.toml`.
Clayton, Cobb, Gwinnett and DeKalb publish owner and sale data free.
Henry, Fayette and Spalding publish parcel shapes but not owners; a Georgia
Open Records request to each county's tax assessor is the free route.
