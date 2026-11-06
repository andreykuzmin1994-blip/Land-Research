# 02 — Building comps: pre-change risk register and dispositions

> The register (BC-01 to BC-20, severity as given) was written by an
> independent reviewer BEFORE the change. Each item below carries what was
> done and the test that pins it. Tests are in `tests/test_building_comps.py`
> unless named otherwise; "live" = `tests/live_building_comps_check.py`
> (own market in a throwaway local database); "golden" =
> `tests/building_comps_golden_check.py` against
> `golden_head_3898047.json`.
>
> Standing risks (`STANDING_RISKS.md`): SR-1 no human-owned file touched,
> `research.py` unchanged; SR-2 all new SQL is module-level constants
> (`test_bc14_new_sql_is_module_level_constants` plus the existing
> whole-surface scan); SR-5 one transaction per table; SR-6 every path on
> fakes; SR-7 snapshots via `mkstemp` + `os.replace`; SR-8 counts only,
> aggregates only; SR-9 delete-then-insert of own rows; SR-10
> `requirements.txt` unchanged; SR-11 no network call at all; SR-14 no new
> `action_type`; SR-15 no metric input.

| ID | Sev | Disposition | Tests |
|---|---|---|---|
| BC-01 | blocking | **Fixed.** `sheet_role` gives each sheet exactly one role (stats, lease, areas, areas+building, land, building) checked in that order; a lease sheet is never an area sheet; `classify_tables` refuses two sheets with the same role (also stats and land). The operator's area sheet keeps its role; with every building column it is also building comps, written after the areas, said on the console and in the README. A lease- or building-only import never calls `write_areas` or `_ensure_submarket` and issues no SQL on `submarkets`. | `test_bc01_*` (7): the shape x classifier table, lease-with-parcel-numbers, area sheet lacking building columns, duplicate refusal (unit and through `run`), no `submarkets` write for lease/building/lease-with-parcels, areas-first ordering with an unchanged area plan; live (a) bbox and notes unchanged |
| BC-02 | blocking | **Fixed.** `cap_rate_fraction`: `%` or a value above 1 = percent; stored as a fraction (documented in code, README and design). Band 3-12% (`CAP_RATE_MIN/MAX`); blank or out-of-band keeps the sale without a cap rate, counted under `cap_dropped` (the sale stays a $/SF comp). The memo reads only the tool's rows and re-applies the band per row; `exit_value_parts` never divides by a cap rate at or below 0. | `test_bc02_*` (4); live (c) "CSV cap 5.5 never counted", "cap rates read are the tool's fractions only" |
| BC-03 | high | **Fixed.** `costar_import._SQL_RESOLVED_SUBMARKET` is a verbatim copy of `research._SQL_RESOLVED_SUBMARKET`, pinned equal; `reporting._SQL_BC_RESOLVED_SUBMARKETS` uses it. Only the new section resolves; the land-comps and market-context reads keep the stored value (byte-identical). | `test_bc03_*` (3); live (c) both resolvers agree per parcel; the parcel stored under Beta shows Alpha's rent |
| BC-04 | high | **Fixed / verified.** `git diff 3898047 -- research.py` is empty. Every `sales_comps` SELECT constant in research.py and reporting.py filters `comp_type`; research.py has no `leasing_comps` string and never names `costar_import` (static guard, and it fails on an injected import). | `test_bc04_*` (3); live (a) every parcel_scores column apart from score_id, scored_at, run_tag, experiment_id equal before and after an import (3 parcels); zoning, site_fit, hazards and adjacency golden checks identical |
| BC-05 | high | **Fixed.** Raw allowlists `BUILDING_RAW_KEYS` / `LEASE_RAW_KEYS`; buyer, seller, tenant, industry and NAICS are stored NULL; contacts, brokers, phones, e-mails and comments are never read. Snapshots hold only the converted (allowlisted) fields. Console: counts and reason labels. Refusals list expected header names only; a sheet with no header row gets the generic refusal listing the expected layouts. Memo: medians, counts, windows. | `test_bc05_*` (5) |
| BC-06 | high | **Fixed.** Kept only: NNN / Triple Net service type, a disclosed starting rent (a `Rent/SF/Yr` column only with `Rent Type`), $2-25/SF/yr. Dropped with reasons: monthly or implausible, not NNN, asking-only, withheld, not a starting rent, rent type not stated. Snapshot label "Signed NNN starting rent from lease comps" next to the unchanged "Submarket asking rent (NNN)". | `test_bc06_*` (4) |
| BC-07 | high | **Fixed.** Module constants with rationale: sold, 36 months, Georgia, Industrial + Warehouse/Distribution (flex, R&D, manufacturing, truck terminal, data hosting, cold storage dropped), 100K-1M SF, built in 15 years, ten sale-condition categories, `_PORTFOLIO_RE` and `Number Of Properties` > 1, price/SF vs CoStar `Price Per SF` within 5%, $30-400/SF. | `test_bc07_*` (2): one row per exclusion (23 rows, every reason counted); hand-computed $/SF and cap medians |
| BC-08 | high | **Fixed.** `exit_value_parts` uses only `site_fit.best_fit` of an OK projection, carries its `rules_label`; income method only when rent and cap rate each have their comps, else $/SF x SF, else nothing; no default cap rate; `$0.1M`; counts, scope, window, method, "an estimate, not an appraisal". Median = average of the two middle values (documented; the land thesis keeps its upper median). | `test_bc08_*` (5) incl. the exact rendered snapshot lines with today injected |
| BC-09 | high | **Fixed.** Origins `costar_import_building` / `costar_import_lease`; DELETE scoped by comp type, origin, `raw ? 'imported_on'`, `NOT (raw ? 'submarket')` (every CSV row has that key) and the market's submarkets; other-origin keys skipped and counted; the importer writes only to the database and its two snapshot files. | `test_bc09_*` (4); live (b): land, CSV and forged-origin CSV rows survive; duplicate stored once; re-import gives the same rows |
| BC-10 | high | **Fixed.** Atomic allowlisted snapshots; replayed and refiltered (size, age, bands, Georgia) whenever an area sheet is imported; no areas = deferred, tables untouched, exit 0; nothing assignable = earlier rows kept; one transaction per table; the console says which steps committed; exit codes 0/1/2/3 documented in the module docstring. | `test_bc10_*` (6); live (e) a rejected value leaves the lease table as it was |
| BC-11 | should-fix | **Fixed.** `ST_MakePoint(lon, lat)` through `_SQL_ASSIGN_POINTS_TO_AREAS`; Georgia box; `_sale_date` + `xlsx_date1904`; Sign Date for recency, Start Date stored; term parsed or NULL; SF precedence RBA > Building SF > Total SF, leases use SF Leased. | `test_bc11_*` (6) |
| BC-12 | should-fix | **Fixed.** Exact (strip + case-fold) alias lists `BUILDING_COLUMNS` / `LEASE_COLUMNS`, every alias recorded in `00_design.md` and `COSTAR_EXPORTS_README.md` with the two CoStar searches, filters and column-picker columns; look-alike sheets with missing columns refused (exit 1) naming them. | `test_bc12_*` (5) incl. "Price Per SF Land" never the building $/SF |
| BC-13 | should-fix | **Fixed.** `MIN_COMPS = 5`, `LEASE_WINDOW_MONTHS = 24`, `BUILDING_SALE_WINDOW_MONTHS = 36` with rationale. Decision: below 5 in the submarket, show the market-wide median labelled "Atlanta market-wide; k in the submarket" when the market has 5, else "—" with both counts. Windows are SQL parameters from the injected date. | `test_bc13_*` (4); live (d) the first day of the window is in, the day before out |
| BC-14 | should-fix | **Not applicable by design.** No new module: the code lives in the protected `costar_import.py` and `reporting.py`, already in every protected list. costar_import imports neither research, runner nor reporting; research.py never names costar_import. | `test_bc14_*` (2); `test_bc04_research_reads_no_leasing_comps_and_never_names_the_tool` (injected import fails the guard) |
| BC-15 | should-fix | **Fixed.** At most four reads per memo or snapshot whatever the parcel count (none beyond two when the market has no tool rows), each in a SAVEPOINT inside an open transaction or followed by a rollback on an idle one; any error is logged and renders nothing. The sales read can use `idx_comps_submarket_date`; `leasing_comps` has no index and needs none (a few hundred rows at most; noted in the code). | `test_bc15_*` (2); live (f) |
| BC-16 | should-fix | **Fixed.** With no tool rows the section and the bundle key are absent. | golden: memo and snapshot through `generate_*` identical to 3898047, only the two new reads added; `test_bc16_no_tool_rows_is_byte_identical` |
| BC-17 | should-fix | **Fixed.** `run_has_baseline` still refuses (exit 3) before any sheet is read. | `test_bc17_building_and_lease_imports_are_refused_after_the_baseline` |
| BC-18 | should-fix | **Fixed.** Building and lease comps are located from Latitude/Longitude only; `FultonLocator` is built only for an area sheet. Fixtures are generated in code ('1 Test St', 99Z parcel ids); no fixture file added. No scraping. | `test_bc18_*` (2) |
| BC-19 | nit | **Fixed.** Dedupe key (address, sign date, SF leased): two suites kept, exact duplicate dropped; subleases and renewals/extensions excluded with counted reasons. | `test_bc19_suites_duplicates_subleases_and_renewals` |
| BC-20 | nit | **Fixed.** `03_decision_note.md` records that the strongest available model ran each role, without naming it, and why. | reviewer check: `grep -riE` for model names over this folder is empty |

## Behaviour changes outside the new feature (for the reviewer)

1. Two sheets with the same role (stats, areas, land) are now refused
   instead of the first being taken (required by BC-01).
2. A sheet with `SF Leased` and parcel numbers was an area sheet before;
   it is now a lease sheet only.
3. The "no CoStar sheet" refusal now lists the expected layouts, and the
   no-database / database-error messages say "comps" and name the
   committed steps.
