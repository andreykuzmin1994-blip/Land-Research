# F10 road frontage: reviewer decision (Tier 1)

Reviewed `eede92c` (screener/frontage.py, run.py, report.py, plan.py,
settings.toml, README.md, tests) against the F10 request.

## Checked

- **Source and access**, re-checked live 2026-11-10: `maps.itos.uga.edu/robots.txt`
  is HTTP 404; the service is public, copyright "Georgia Department of
  Transportation, ITOS-CVIOG-UGA", maxRecordCount 1000. A probe on a Fulton
  Industrial Blvd box gave SR 70 (minor arterial) and local roads; the
  module measured about 529 ft of frontage. The layers' scale ranges
  (local roads up to 1:100,000) are far above the identify extent's scale,
  so no class is dropped. osmnx is not used.
- **Leak rule**: `attach_frontage` runs after the sort, F6, F7 and F9;
  roads are not in `layers.LAYERS`, `map_checks` or `checks.py`; spy tests
  cover the scorers, `site_rejects` and `flags` with slope on and off.
  rank.py, scoring.py, screens.py and autoresearch/ are untouched.
- Three states, fail soft, workers 1-3, `[frontage]` table, README, curb
  cut as a manual next step in the brief: all present and tested.

## Fixed

1. NaN / infinite road coordinates passed validation (Python's JSON reader
   accepts `NaN` and `Infinity`). They are now a malformed answer: not
   checked, not cached.
2. A geometry error inside `measure` (GEOS / ValueError) would have
   crashed the whole run. It now gives "not checked: could not measure the
   road answer".
3. The two carriageways (INC / DEC) of a divided county or city road were
   listed as two roads. The direction is dropped from the route id label,
   so they merge (state routes already merged).
4. Wording: "freeway/expressway" frontage was called "no driveway".
   Expressways can have partial access control, so the brief and README
   now say "access controlled: a driveway is unlikely".
5. The "none" brief line now also names a private drive GDOT doesn't map
   as a cause, next to "possibly land-locked".

Tests added for 1-3. Suite: 398 tests, OK, offline.

## Accepted as is

- The 50 ft buffer can miss frontage on wide rights-of-way (100 ft+); the
  caveat says so, and the request asked for ~50 ft.
- The corner over-count (up to ~50 ft per end) is documented.

Decision: accept with the fixes above.

## Post-change: function types (private roads, ramps)

Defect: the only access filter was the layer (interstate / freeway), and
the "SR N" label read only the system digit of the route id. GDOT's
FUNCTION_TYPE (route id character 0; domain from
`rnhp.dot.ga.gov EOC_SUPPORT_LAYERS/MapServer/7`) was ignored, so a private
road on the local-road layer (`9003300031500INC`, about 1,678 such features
live) gave ~1,019 ft of public frontage, and a ramp (`20001008042F0INC`)
gave access frontage under a made-up "SR 8042 F0".

Fixed in `frontage.py` (`function_type`, `road_kind`):

- Types 2/3/4/7/8 (ramp, collector distributor, ramp-CD connector, managed
  facility, Y-connector) are access controlled on any layer: they go to
  `frontage_limited_ft`, never `frontage_ft`.
- Type 9 (private) goes to a new column, `frontage_private_ft` (report
  only, attached after the sort like the rest; same `frontage_` stem, so
  the spy and stem guards cover it). The brief says "along a road GDOT
  maps as private (not public access: check an access easement)".
- Type P (projected alignment, not built) is left out.
- "SR N" only for a main line (type 1); anything else shows its GDOT
  route id. The route-id pattern now accepts the letter types P/Z/R.
- Types 1/5/6/Z/R and ids not in GDOT's 16-character form stay access
  frontage, as before. Z ("Other", 931 on the local layer) is unknown
  rather than shown non-public, so it is kept; the caveat already says a
  close road is not legal access.

Three states hold for the new column: a number, `0`, or empty when not
checked. Tests added: private alone, private next to a public road, every
limited type on layers 2/3/6, projected left out, public types kept,
`road_kind`, ramp / non-main-line labels. Suite: 404 tests, OK, offline.
README and the `[frontage]` settings comment updated.
