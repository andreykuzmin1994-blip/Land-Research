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
| `ranked.csv` | Every surviving parcel, best first, with the facts behind its score |
| `map.html` | Open in a browser: survivors coloured by score, click for details |
| `briefs/` | One page per top parcel: site, owner, why it ranks, watch-outs, next steps |
| `rejected.csv` | Every parcel screened out, with the reason |
| `ranked.geojson` | The same survivors for GIS tools |

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

## Tune it

- `screener/settings.toml`: acreage, which cities to search, and every
  screen threshold (floodplain %, wetlands %, slope, usable acres, which
  owners to exclude). Optional screens: `max_exit_miles` and
  `exclude_hard_entitlement` (drop land zoned and planned residential).
  `exclude_association_owned` drops land held by homeowners', property
  owners', community, neighborhood, town, townhome, condominium or civic
  associations: subdivision common areas, not land for sale. The owner
  name must pair an owners' word with an association word ("... HOMEOWNERS
  ASSN INC"), or carry HOA inside a company name ("... HOA INC"); ASSOC,
  ASSOCIATION or POA alone don't count.
- `screener/inputs/decisions.csv`: mark parcels `pass`, `listed` or
  `pursue`. Passed and listed parcels drop out of future runs. See
  `inputs/README.md`.
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
| Slope, relief | USGS 3DEP lidar (2018–19), resampled to 10 m |
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

## AutoResearch

`screener/autoresearch/` runs Karpathy's keep-or-revert loop on `rank.py`
against an answer key it can't game: Fulton land (as of the 2016 tax
digest) that its owner sold in 2016 or later, and that carried a $1M+
industrial building by 2025. That's 51 sites, spatially split 39 dev / 12
test. See `screener/autoresearch/PROGRAM.md`.

Baseline (2026-10-28): the hand-built ranking puts 2 of the 39 dev sites
in its top 2% (46 parcels). Random ranking averages 0.9. The developer
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

- `rank.py` breaks the purity rules. It is checked before it is imported,
  so a refused file never runs, and nothing is scored. It may import only
  `__future__`, `math` and `typing`; may not use `open`, `eval`, `exec`,
  `compile`, `getattr`, `globals`, `vars`, `str.format`, frame attributes
  (`gi_frame`, `f_back`, `f_locals`) and the like, dunder names, private
  names, or `global` / `nonlocal`; and its strings may not name the
  evaluation files. This catches drift. It is not a sandbox.
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
