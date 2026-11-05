# Site fit — design

> **Tier 2** (touches the strategy-fit input of the metric and the
> mutability contract). The pre-change risk register and the disposition of
> every item is `02_risk_review.md`; the decisions the post-change review
> must check are in `03_decision_note.md`. Model names are omitted.

## Why

The operator plans buildings in their own SiteFit tool (a single-page
Leaflet/turf app). They asked to use it "for potential projections": what
building of their standard prototypes a parcel can hold, in the memo and,
once zoning is verified, in BTS strategy fit.

## Pieces

1. **`building_prototypes.json`** (repo root, human-owned). SiteFit's
   defaults, copied by hand (the tool's file is not committed and is
   gitignored):

   | | cross-dock | front-load | rear-load |
   |---|---|---|---|
   | L x W (ft) | 1,000 x 520 | 640 x 260 | 640 x 260 |
   | clear height | 40 ft | 36 ft | 36 ft |
   | dock sides | both | front | rear |
   | per dock side | 130 ft court + 55 ft trailer parking | same | same |
   | envelope depth | 890 ft | 445 ft | 445 ft |
   | min length (40 ft steps) | 600 ft (312,000 SF) | 400 ft (104,000 SF) | 400 ft |

   Dock doors every 12 ft with 60 ft end clearance, trailer stalls 12 ft
   wide; parking lots (car 9 x 18 with 24 ft aisle, trailer 12 x 55 with
   80 ft aisle), detention (400 x 200, 6 ft, 4:1) and drive widths (30 /
   24 ft) are recorded for later use. Default setback 25 ft.
   `"source": "operator SiteFit tool defaults"`, `"as_of": "2026-11-05"`.

2. **`site_fit.py`** (protected; imports only the standard library and
   `zoning`). Loads and freezes the file exactly like `zoning.py` does
   `zoning_rules.json`. `project_parcel(conn, parcel_id)` /
   `project_parcels(conn, ids)`:
   - reads the parcel's stored zoning and resolves its district through
     the new `zoning.district_for_stored` (the same lookup as S9);
   - uniform setback = largest non-null front/side/rear of the district,
     on every side (frontage is unknown), else 25 ft;
   - inward buffer **in PostGIS** (Z-10): `ST_CollectionExtract(
     ST_MakeValid(geometry), 3)`, `ST_Transform` to SRID 2240 (NAD83 /
     Georgia West, US survey feet), `ST_Buffer(-setback)`, GeoJSON rounded
     to 0.01 ft; gross area from geography;
   - fits each prototype's whole envelope in pure Python (no new
     dependency, SR-10): angles = a 2 degree grid plus the directions of
     the 32 longest edges (and +90); per angle a slab sweep over vertex
     events, pruned by probe lines every 25 ft (each line through a fitted
     rectangle must carry an inside chord at least as long); a placement
     is accepted only when no boundary edge of any ring enters the
     rectangle widened by 0.01 ft and its centre is inside (even-odd), so
     holes, notches and multipart parcels are exact;
   - shrinks the length on the 40 ft lattice; the district's
     `max_lot_coverage_pct` (or FAR) over gross geography area caps it;
   - counts docks and trailer stalls with SiteFit's `geomBuilding`
     formulas.

   The result is a frozen `Projection` with a status (`ok`,
   `empty_after_setbacks`, `no_geometry`, `error`, `unavailable`), the
   rules label (draft / verified / no district rule), and the constraints
   applied, always including "residential buffers not applied",
   "flood/wetlands not deducted" and "car parking not modelled".

3. **`research.py`**: BTS only, verified only (see the decision note).
4. **`runner.py`**: `site_fit.verify_prototypes_committed()` before every
   experiment.
5. **`reporting.py`**: snapshot "Development Potential" and one line per
   memo highlight, computed at read time (no schema change).
6. **`make site-fit`** (`cli.py site-fit`): read-only projections for
   `--parcel ID` or `--top N`; `--export` writes SiteFit study files
   (`saveStudy`/`loadStudy` shape) to the gitignored `site_fit_studies/`.

## Not modelled (stated on every projection)

Adjacency buffers, residential or toward other districts (neighbour
coverage in the database is incomplete: only discovered 5-50 ac candidates
are stored; each buffer the district's rules list is named on the
projection), height (always stated: the clear height, plus the district
maximum when the rules have one), stormwater detention (SiteFit's default
400 x 200 ft pond is not placed), car parking, truck drives and
circulation, flood (H4) and wetlands (H6). Amended after the post-change
review (03_decision_note.md, findings 2, 7, 11, 13). The H4/H6 hook is the private
`site_fit._flood_wetland_exclusions`, which returns nothing today.
