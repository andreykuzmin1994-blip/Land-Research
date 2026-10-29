# Decision Note — Screener owners' associations (follow-up to screener-rules residual 4)

> Screener-only change (`screener/`, governed by `screener/README.md`), a
> follow-up to `07_screener-rules_decision_note.md` § "Residual gaps" 4.
> Between runs; no AutoResearch run active. Base `9269736`. Two roles, each
> in an independent fresh context on the session's strongest available
> model: implementation, then adversarial review + commit (this note).
> Files: `screener/owners.py`, `screener/tests/test_rules.py`, one line of
> `screener/README.md`, one word in `screener/run.py`'s funnel label, and
> this note. **Verdict: approved as submitted. The reviewer made no code
> changes.**

## Why

The operator's default is that land owned by homeowners', property owners'
or community associations is not buyable industrial land. A full live Fulton
run ranked parcels under 114 association-like owner names. At `9269736`,
`owner_type` typed 95 of them `association` and missed 14 real ones. It also
typed two `ASSOC` partnerships and a church conference `individual`.

## What changed (`owners.py`)

1. **`_ASSOCIATION_RE`** now also matches two forms:
   - Any owners' word right before ASSOCIATION, ASSN or ASSOC. This covers
     misspelt and glued forms (`UNIT OWNERS`, `INDUSTRIAL PARK OWNERS`,
     `HOMEOWNRS`, `HOMEOWERS`, `HMOWNERS`, `HOMESOWNERS`, `TOWNHOMEOWNERS`,
     `OWNERS'`).
   - `TOWN ASSOCIATION` / `TOWN ASSN`, glued forms included (`SERENBETOWN`).

   It does not match `TOWN ASSOC`, which usually means associates. It also
   does not match words ending in OWERS other than HOMEOWERS, such as
   `TOWERS` or `POWERS`.
2. **`_PARTNERSHIP_RE`**: if a name has `LP`, `L P`, `LLP`, `LLLP` or `LTD`,
   it is never an association. This closes the 07 residual `<builder> HOMES
   ASSOC L P`.
3. **`_ENTITY_RE`** gains `ASSOC`. ASSOCIATES and ASSN were already in it.
4. **`_RELIGIOUS_RE`** gains `ADVENTISTS?` and `CONFERENCE
   ASSOCIATION/ASSOC/ASSN`. CONFERENCE alone still doesn't count.
5. **Other files:**
   - `run.py`: the funnel label is now "not built on, not government /
     developer / association / exempt".
   - `README.md`: "town" is added to the list of association kinds.
   - Tests: a new class, `OwnersWordAssociationTests`, with 6 tests. Run
     against `9269736`, 5 of them fail. The sixth is a guard (place-name
     associations stay `entity`) and must keep passing.

## Before/after (reviewer re-run: base `owners.py` from `9269736` vs the patched one)

- **The 114 names.**
  - Before: 95 association, 16 entity, 3 individual. After: 109
    association, 4 entity, 1 religious.
  - 14 become association: ASBURY PARK HOMEOWNRS, CREEL WALK HOMEOWERS,
    DUPONT COMMONS HOMESOWNERS, COMMUNITIES AT PRINCETON LAKES HMOWNERS,
    FIELDSTONE MANOR TOWNHOMEOWNERS, CASCADE UNIT OWNERS, GIVERNY and
    TAPESTRY AT LE JARDIN OWNERS, VILLAGES OF EAST POINT OWNERS, OAKLEY
    INDUSTRIAL PARK OWNERS, and the 4 SERENBE TOWN / SERENBETOWN spellings.
  - Both CHESTER INDUSTRIAL PARK ASSOC names go from individual to entity.
  - SOUTH ATLANTIC CONFERENCE ASSOC goes from individual to religious.
  - These stay `entity`: ACKERMAN MIDTOWN ASSOC LTD and COLDWATER CREEK
    ASSOCIATION INC.
  - None of the 95 loses `association`.
- **Base-run `ranked.csv` + `rejected.csv`** (3,713 distinct owners, 5,367
  parcels). 24 owners change type: the 17 above, plus 7 that appear only on
  rejected parcels:
  - CAMP CREEK BUSINESS CENTER OWNERS ASSN INC: association
  - CAMP CREEK POINTE OWNERS ASSOCIATION INC: association
  - COMM AT PRINCETON LAKES HMOWNERS ASSN INC THE: association
  - FOUR FIFTY PIEDMONT ASSOC: entity
  - three more spellings of the Seventh-day Adventist conference: religious

  No owner moves to or from `developer`, `government`, `utility`, `estate` or
  `trust`.
- **Ranked parcels.** 31 of the 1,747 change type:
  - 26 become `association` and would now be rejected. SERENBE TOWN accounts
    for 14 of them, most with a land value of $100.
  - 4 CHESTER parcels lose the individual-owner boost.
  - 1 conference parcel gains the religious flag.

  No parcel moves from rejected to ranked.

## False-positive audit

- **CSV owners.** The reviewer read all 24 changed names by hand. Every one
  is an owners' association, a planned-community association, an ASSOC
  partnership or the Adventist conference.
- **Cached parcel owners** (5,544 names). 38 change:
  - 30 become association. Each has an owners' word (including the misspelt
    forms) before ASS..., or is a SERENBE TOWN name. They include office and
    business-park owners' associations (SANCTUARY PARK,
    PARK BRIDGE, 360 TECH VILLAGE, RESURGENS PARK). Their land is common
    area, the same case as OAKLEY INDUSTRIAL PARK.
  - 4 go from individual to entity (ASSOC).
  - 4 become religious.
- **All 791,537 deed-grantor names.**
  - 300 go from individual to entity, all through ASSOC: banks ("NATIONAL
    ASSOC"), associates partnerships and trade bodies. None is a person.
  - 19 become association. The only one that isn't an HOA or an owners'
    association is the trade body AMERICAN HOTEL OWNERS ASSOCIAT.
  - 4 become religious.
  - No name loses `association`.
- **`same_owner`.** None of the 18,265 distinct deed-grantor/owner pairs
  changes. The pairs cover all 7,891 cached parcels.
- **Plain `evaluate`** is unchanged: pool 2318, positives 39, k 46, hits 2.
  The key is frozen.
- **Edge cases the reviewer probed** with made-up names. None of the first
  three forms appears in the roughly 797k real names checked:
  - The TOWN branch has no word boundary on its left, which is what lets it
    catch SERENBETOWN. So `MIDTOWN ASSOCIATION INC`, `DOWNTOWN ASSN` and
    `GEORGETOWN ASSOCIATION OF REALTORS` would read as associations.
  - Any LP, L P or LTD token removes `association`. For example, a
    hypothetical `... HOMEOWNERS ASSOCIATION LTD` would become entity.
  - ASSOC now counts as company context for the developer-surname rule.
    `EXETER ASSOC` would read as developer, as `EXETER ASSOCIATES` already
    did.
  - ADVENTIST alone reads as religious, so a hospital system such as
    `ADVENTIST HEALTH SYSTEM ... CORP` would too. That only adds a flag. In
    the real names, ADVENTIST alone appears only on churches (ATLANTA KOREAN
    ADVENTIST, BEREAN SEVENTH DAY ADVENTIST).

## COLDWATER CREEK ASSOCIATION INC stays `entity`

The parcel looks like a subdivision common area: the site is `0 ELK HORN TRL
# CA 1`, the land value is $100, and the mail goes to a management company.
But the name has no owners' or community word. The same `<place>
ASSOCIATION INC` form also names other bodies in the county's own files
(GEORGIA MUNICIPAL, BRIGHTER DAYS, RIVERWOOD, ROXBORO VALLEY, ALBEMARLE). A
wrong `association` tag hides a parcel, so the rule that reads only the name
stays conservative.

The parcel ranks 1,508 of 1,747. Two ways to deal with it:
- the operator marks it `pass` in `inputs/decisions.csv`;
- a parcel-level common-area signal (a `# CA n` or COMMON AREA site with a
  nominal land value) is added in `fulton.py` or `screens.py`. Those files
  are outside this change.

## Residual gaps / human follow-ups

1. **Frozen key.** The answer key still holds the old `owner_type`, so the
   gap from note 07 residual 1 grows. A human needs to rebuild the key with
   `build_eval` and then run `--decide --new-baseline`. Neither was run here.
2. **Forms still not matched:**
   - singular `OWNER ASSOC(IATION)` (ATLANTIC STATION MASTER OWNER ASSOC INC);
   - `MASTER ASSN`;
   - `COMM ASSN`;
   - `PROPERTY ASSOCIATION`;
   - `HOME OWNERS CORP`;
   - `RECREATION ASSN`;
   - place-name `<X> ASSOCIATION INC`;
   - deed names cut short at `ASSOCIA`.

   In the base run, every parcel with one of these owner forms was already
   rejected for other reasons, except COLDWATER CREEK (covered above). One
   more ranked case has no association word at all: `PEACHTREE HILLS PLACE
   MASTER CONDOMINIUM`, ranked #473. It is typed `individual`, so it gets the
   individual-owner boost. It is left for a human decision.
3. **Spaced partnership forms.** Names with `L P`, `L L P` or `LLLP` and no
   other company word still type as `individual`. That is 35 owners in the
   base CSVs, for example POST APARTMENT HOMES L P. One of them, FOOTE
   MARTHA L P, is ambiguous. This predates the change.

## Verified (reviewer)

- **Tests.**
  - Root suite: 847 OK.
  - Screener suite: 96 OK (90 + 6).
  - `python -m screener.tests.test_screener`: 23 OK.
  - The new class run against `9269736`: 5 of 6 fail; the guard passes.
- **Code checks.**
  - The changed `.py` files parse with `feature_version=(3, 11)`.
  - Standard library only.
  - No model names.
- **Safety.**
  - Offline only.
  - `--decide`, `--new-baseline`, `--test` and `build_eval` were not run.
  - The real repo was only read (`screener/cache`).
