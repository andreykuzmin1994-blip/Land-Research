# Land screener

Finds 5–50 acre industrial-development land in Fulton County that is
**buyable** (private, long-held, not developer- or government-owned) and
**buildable** (outside floodplain and wetlands, workable slope, near an
interstate, plausible zoning). It ranks what's left and writes a one-page
brief for each top parcel. It uses free public data only: no CoStar, no
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

Map data is cached for 7 days (`screener/cache/`), so re-runs take a few
minutes. Use `--refresh` to force fresh downloads.

## Tune it

- `screener/settings.toml`: acreage, which cities to search, and every
  screen threshold (floodplain %, wetlands %, slope, usable acres, which
  owners to exclude).
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
  No machine-readable zoning exists, so the brief says "unknown".
- **Wetlands.** The national inventory dates from the 1980s and misses
  small and forested wetlands. A field delineation is still needed.
- **Whether it's for sale.** No free listings feed can be used legally. Mark
  listed parcels in `decisions.csv`. CoStar's license bars automated
  extraction and loading its data into databases or AI tools without
  written consent, so this tool doesn't touch CoStar.
- **Owner contacts.** Mailing addresses only. LLC officers need a manual
  Georgia Secretary of State lookup.
- Distances are straight-line, not drive time.

## AutoResearch

`screener/autoresearch/` runs Karpathy's keep-or-revert loop on `rank.py`
against an answer key it can't game: the Fulton land that owners sold, in
2016–2024, to buyers who then built industrial buildings on it (48 sites,
spatially split into dev/test). See `screener/autoresearch/PROGRAM.md`.

Baseline: the hand-built ranking finds 1 of 38 dev sites in its top 2%,
about what random ranking does. The developer sites sit much closer to
exits and inside industrial areas, and were mostly sold by companies, so
there is a lot for the loop to learn.

```bash
python -m screener.autoresearch.build_eval          # rebuild the frozen key (humans only, ~5 min)
python -m screener.autoresearch.evaluate --decide   # baseline, then once per experiment
python -m screener.autoresearch.evaluate --test     # sealed test split (humans only)
```

To run the loop, open a fresh Claude Code session and say: "Read
screener/autoresearch/PROGRAM.md and run 15 experiments."

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
