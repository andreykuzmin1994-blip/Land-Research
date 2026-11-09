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
python -m screener.run                       # full Fulton run, ~15-25 min the first time
python -m screener.run --limit 200           # quick trial
```

Results land in `screener/output/fulton-<date>/`:

| File | What it is |
|---|---|
| `summary.md` | The funnel (how many parcels each screen removed), top 10, common reject reasons |
| `ranked.csv` | Every surviving parcel, best first, with the facts behind its score (plus a rough cut/fill estimate and a building-fit check that aren't scored; see Earthwork and Site fit below) |
| `map.html` | Open in a browser: survivors coloured by score, click for details |
| `briefs/` | One page per top parcel: site, owner, why it ranks, watch-outs, next steps |
| `rejected.csv` | Every parcel screened out, with the reason |
| `ranked.geojson` | The same survivors for GIS tools |
| `features.json` | The evaluation's features for each ranked parcel, kept for your decisions (see Decisions below) |

Map data is cached for 7 days and the county deed file for 30
(`screener/cache/`), so re-runs take a few minutes. `--refresh` forces
fresh downloads of both. If a map layer is down, or sends back a malformed
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
the map shape and the brief says so. Worst case is under a second per
parcel (a shape where nothing fits); `[fit] max_parcels` caps it to the
top N by rank. Like earthwork, it is computed only after scoring, flags
and the sort, and is in no score, screen or flag. To make it a learned
feature or a screen, a human would add it to `build_eval.FEATURES`,
rebuild `data/` and take `--new-baseline`; reading it from `rank.py` is
not the way.

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
map layers, county parcels and slope answers are kept for 7 days, the deed
file for 30.

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

Some names mean slightly different things live and in the evaluation:
`usable_after_slope` is slope-adjusted live but equals `usable_acres` in
the evaluation; `rail_ft` / `rail_owner` come from the whole live rail
layer but only main lines and yards in the evaluation; ownership fields
are as of today live and as of 2016-01-01 in the evaluation.

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
  `[owners] adjacency_gap_ft` sets how close two same-owner parcels must be
  to count as adjacent (see Owner clusters below; flags only).
  `[fit] enabled` and `max_parcels` switch the building-fit check off or
  limit it to the top N ranked parcels (see Site fit; report only).
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
  -> deed history                    (county CAMA extract)
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
  human evaluation step: build the same exact-key groups on the 2016
  snapshot in `build_eval.py` (owner and mailing address exist there), add
  a FEATURES entry, and rebuild with `--new-baseline`. Do not read the
  flag text from `rank.py`.

## AutoResearch

`screener/autoresearch/` runs Karpathy's keep-or-revert loop on `rank.py`
against an answer key it can't game: Fulton land (as of the 2016 tax
digest) that its owner sold in 2016 or later, and that carried a $1M+
industrial building by 2025. That's 46 sites, spatially split 34 dev / 12
test. See `screener/autoresearch/PROGRAM.md`.

Key rebuilt 2026-10-29 after the owner and association screens changed:
the hand-built ranking puts 2 of the 34 dev sites in its top 2% (45
parcels). Random ranking averages 0.8. The developer
sites sit much closer to exits and inside industrial areas, and were
mostly sold by companies, so there is a lot for the loop to learn.

```bash
python -m screener.autoresearch.build_eval          # rebuild the frozen key (humans only, ~5 min)
python -m screener.autoresearch.evaluate --decide   # baseline, then once per experiment
python -m screener.autoresearch.evaluate --test     # sealed test split (humans only)
python -m screener.autoresearch.evaluate --decide --new-baseline   # humans, after changing the key or screens
```

To run the loop, open a fresh Claude Code session and say: "Read
screener/autoresearch/PROGRAM.md and run 15 experiments."

The evaluator refuses (prints `REFUSED: ...`, exits 3, logs nothing) when:

- `rank.py` breaks the purity rules (below). It is checked before it is
  imported, in every mode but `--control`, so a refused file never runs
  and nothing is scored.
- (`--decide`) there are uncommitted changes or untracked files under
  `screener/` (ignored files such as `cache/` don't count), the experiment
  commit touches any file besides `screener/rank.py`, or git can't be read.
- (`--decide`) any score is NaN or infinite, or outside 0–100.
- (`--decide`) the evaluation changed since the best kept run (`evaluate.py`,
  `screens.py`, `data/`), or the best kept run's record is broken:
  `best_ranking.json` is missing, unreadable or from another run, or
  `experiments.tsv` has kept runs, none of which is in this branch's
  history. Deleting one of those files can't reset the bar.

In each of the last cases a human re-records the baseline with
`--decide --new-baseline` (at the best kept commit when only its record
was lost). A new baseline starts a new chain: later experiments are
compared with it, not with older runs.

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
  `get_type_hints` and `ForwardRef`; dunder names (`__x__`); private (`_x`)
  attributes;
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
- `global` and `nonlocal`;
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
- A planted `screener/__pycache__/rank.*.pyc` whose recorded timestamp and
  size match `rank.py` would run instead of the checked source. git ignores
  it, so `--decide` doesn't see it.
- The file could change between the check and the import.
- Changing shared objects through their methods (such as registering a
  class with an ABC) isn't detected.
- The live `python -m screener.run` doesn't run the check.
- Python 3.14 adds public evaluation hooks (`annotationlib`,
  `ForwardRef.evaluate`, template strings) that these rules haven't been
  reviewed against.

## Tests

```bash
python -m unittest discover -s screener/tests -t .
```

## Adding a county

Copy `fulton.py` to `<county>.py` with the same functions
(`fetch_parcels`, `load_cama`, `fetch_industrial_points`,
`ZONING_SOURCES`) and set `county = "<county>"` in `settings.toml`.
Clayton, Cobb, Gwinnett and DeKalb publish owner and sale data free.
Henry, Fayette and Spalding publish parcel shapes but not owners; a Georgia
Open Records request to each county's tax assessor is the free route.
