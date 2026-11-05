# Zoning specifics — design draft

## Facts from the spike (2026-11-05, read-only, public GIS)

The screener's per-city zoning sources were run against the 1,075 corridor
parcels. **1,072** got a district.

| Jurisdiction | Parcels | Industrial districts present |
|---|---|---|
| City of Atlanta | 335 | I-1 (26), I-2 (15), I-1-C (8) |
| City of South Fulton | 365 | M-1 (95), M-2 (91) |
| Fairburn | 239 | M-2 (65), M-1 (22) |
| Union City | 112 | M-1 (16) |
| Fulton Industrial District | 24 | M-2 (19) |

Screener entitlement classes for these parcels:

| Class | Parcels |
|---|---|
| by right | 357 |
| likely | 29 |
| possible | 223 |
| hard | 463 |
| unknown | 3 |

## Pieces

1. **`zoning_sources` (registry entries).** The six layers the screener
   already uses, written in the `sources.json` shape. `sources.json` is
   human-only, so the entries are drafted for the operator to paste.
2. **`zoning_rules.json` (human-owned facts, cited).** For each jurisdiction
   and district:
   - use status for warehouse/distribution (by right / conditional / not
     permitted);
   - setbacks, height, coverage or FAR;
   - residential-adjacency buffers and height-based extra setbacks;
   - landscape strips, parking and loading.

   The agent never edits it, like `sources.json`. A test pins that rule.
3. **`zoning_import.py` (operator tool, between runs, immutable during a
   run).**
   - Reads the sources and looks up each parcel's district, using a join on
     parcel id or the largest polygon overlap, at 1 req/s (SR-11).
   - Writes `parcels.zoning` / `zoning_description`.
   - Classifies against `zoning_rules.json`.
   - Prints counts only.
4. **`research.py` (Tier 1, between runs).**
   - Discovery upsert keeps an existing zoning value:
     `COALESCE(EXCLUDED.zoning, parcels.zoning)`.
   - H3 stops being a stub: a residential district without an industrial
     future land use is flagged (later possibly a reject).
   - S9 follows the program.md table: 10 by right, 7 conditional, 4 rezoning
     with industrial precedent nearby, 1 otherwise.
5. **Building test fit (later, needs the operator's building preferences).**
   - Inward buffer by setbacks, larger next to residential (district rules).
   - IBC §507 60 ft yards and IFC §503 access.
   - Minus flood, wetland and steep areas.
   - Then fit rear-load, cross-dock and small-bay footprints.

## Integrity

- **Honest limit on separation:** `research.py` runs with the same database
  credentials as everything else, so a table split gives no real
  separation.
- **What is protected:** the facts (sources, rules) and the code that
  fetches them. How S9 *uses* them stays the loop's to improve, under the
  program.md "never fabricate" rule.
- **Tier:** the mutability contract gains a protected data file and an
  immutable module, so the change is Tier 2.
