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
