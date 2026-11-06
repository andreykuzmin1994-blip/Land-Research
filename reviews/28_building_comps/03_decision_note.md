# 03 — Building sale and lease comps: decision note

> **Tier 2.** Design `00_design.md`; pre-change register and the
> disposition of every item `02_risk_review.md`. This note records the
> change, the evidence, the live numbers (counts only), what a human will
> notice and what a human must do. Model names are omitted.

## Who ran which role (BC-20)

`STANDING_RISKS.md` (Tier 2) asks to record which model ran each role. The
stage rule for this repository forbids model names in files (they date
quickly and are not part of the audit trail that matters), so this note
records the requirement instead: the strongest available model ran each
role (independent pre-change risk enumeration, implementation, and the
post-change adversarial review), each in its own fresh context. The
pre-change register BC-01 to BC-20 came from a context that did not write
the code; the post-change review (round 1, below) came from reviewer and
skeptic contexts that did not write it either, and each finding was
reproduced by a second context before it was accepted.

## Verdict

| Step | Who | Result |
|---|---|---|
| Pre-change risk register (BC-01 to BC-20) | independent reviewer, fresh context | 20 items: 2 blocking, 7 high, 9 should-fix, 2 nits; every one disposed (one by design: no new module) |
| Implementation | implementer | memo only; no scoring input; byte-identical memo and snapshot without tool rows |
| Post-change adversarial review, round 1 | independent reviewer + skeptic, fresh contexts | 11 findings (1 high, 5 should-fix, 5 nits), every one reproduced; 10 fixed with tests, 1 (finding 8, pre-existing) deferred as a separate Tier 2 change |
| Round-1 fixes | implementer | both suites, five golden checks and the live check re-run: all pass |

**Ship as memo-only.** Nothing changes for an operator who has not imported
a building or lease export; scoring is byte-identical to 3898047.

## Review round 1

| # | Sev | Finding | Disposition | Test(s) |
|---|---|---|---|---|
| 1 | should-fix | a building/lease look-alike missing columns refused the whole workbook, so stats, areas and land were not imported | **Fixed (option b).** `sheet_role` never raises; a look-alike gets `incomplete-building` / `incomplete-lease` with a message naming expected headers. With another matching sheet it is named ("building: NOT imported: ...") and the rest imports (exit 0); alone it is refused (exit 1). Two sheets of one role still refuse. | `LookalikeSheetTests.test_f1_*` (3) |
| 2 | high | a size column under an unknown name ('Bldg SF', 'Leased Square Feet') was ignored silently, or refused with a generic message naming no missing header | **Fixed.** A sheet with no marker that still has `Sale Price` with `Sale Date`/`Sale Status`, or a lease-only header, is a look-alike: the message lists every missing required column with all aliases (the size aliases included). A land sheet missing a land header also names what the Land layout lacks. `EXPECTED_LAYOUTS` lists every marker alias. Unrelated sheets are still ignored. | `test_f2_*` (4) |
| 3 | should-fix | a comp a CSV already delivered was skipped by the importer and never read by the memo, so it counted nowhere | **Fixed.** The tool stores its filtered row too and counts it ("N also in a CSV-delivered row, stored here too because the memo's medians read only this tool's rows"). Nothing else reads building or lease comps, so nothing double counts. A CSV re-ingest that deletes the tool's copy is restored by the next workbook import (README). | `test_bc09_a_comp_a_csv_also_delivered_is_counted_and_still_stored`; live (b) both rows kept, (c) medians over 6 sales |
| 4 | should-fix | leases had no building-age rule while sales did, mixing old-stock rents with new-stock cap rates | **Fixed.** `BUILDING_MAX_AGE_YEARS` also drops a lease whose `Year Built` is given and older ("building not built within 15 years"); a lease without it is kept (the column is optional); the refilter applies it to replayed leases; the memo note says so. | `LeaseAgeTests` (4) |
| 5 | should-fix | a land sheet with no qualifying sale aborted the building and lease import of the same workbook | **Fixed.** With a building or lease sheet present, the land sheet is treated as absent and the console says so; without one, the refusal is exactly as before (nothing written, stats CSV included). | `EmptyLandSheetTests` (2) |
| 6 | nit | docs quoted the old no-database message | **Fixed** in `COSTAR_EXPORTS_README.md` and the module docstring. | — |
| 7 | nit | the tool-row predicate is a convention | **Documented** in `00_design.md` item 8: hand-written or other tools' rows must not use the tool's origins. | — |
| 8 | nit | the baseline refusal reads only the TSV, not the database mirror (pre-existing) | **Deferred.** Pre-existing for land and areas; the reviewer marked it optional and a separate Tier 2 change. Recorded in `00_design.md` "Not done". | — |
| 9 | nit | 'Leased SF' on a building sheet made it a lease sheet and refused the workbook | **Fixed.** A lease sheet needs a lease marker AND a lease-only header (`Sign Date`, `Service Type`, `Rent Type` or a rent column). | `test_f9_*` |
| 10 | nit | the exit value's assumptions were unstated | **Fixed.** `_BC_NOTE` and the exit line state: stabilised, 100% leased at the median signed NNN starting rent, no vacancy, credit loss or reserves, cap rate at sale on in-place income (reads high when rents have risen). No vacancy constant added: that would be a new valuation assumption for a human to choose. | `test_f10_*`; `test_bc08_exact_rendered_snapshot_lines` |
| 11 | nit | (a) market-wide line said "n=0 in the submarket"; (b) trailing-time / month-name dates dropped; (c) '5.5% (Est)' counted as blank; (d) phrase forms of Rent Type / Secondary Type dropped; (e) address-less sales matched across origins | **Fixed.** (a) no submarket count without a submarket; (b) `comp_date` on building and lease sheets only (the land parser is unchanged, so land imports stay as before); (c) "cap rate not a number" (`-`, `n/a` stay blank); (d) `Starting Rent` / `Asking Rent` and `Warehouse/Distribution` read, `Warehouse/Flex` still dropped; (e) an empty address never matches across origins. | `ParsingDetailTests` (3), `test_f11a_*`, `test_bc09_an_address_less_comp_is_never_matched_across_origins` |

## Change

- **`costar_import.py`** (protected): sheet roles (`sheet_role`,
  `classify_tables`), exact alias lists, `convert_building`,
  `convert_lease`, `cap_rate_fraction`, `lease_term_months`, refilters,
  atomic allowlisted snapshots, `write_comps` (one transaction per table,
  own rows only), the resolver copy, `run()` extended (areas first, then
  land, building, lease; exit codes documented).
- **`reporting.py`** (protected): `fetch_building_comps` (at most four
  SAVEPOINT reads, windows from the injected date), `comp_stat`,
  `comp_median`, `exit_value_parts`, the snapshot Market Context lines and
  the memo section, both omitted with no tool rows; `generate_snapshot`
  takes an optional `today`.
- **Tests**: `tests/test_building_comps.py` (87 after round 1), golden case + check +
  record of 3898047, `tests/live_building_comps_check.py` (added to
  `.github/workflows/discovery-fulton.yml`).
- **Docs**: `COSTAR_EXPORTS_README.md` (the two CoStar searches, filters,
  columns, what is stored, sheet roles, where it shows),
  `COSTAR_INGESTION_CONTRACT.md` (Excel path notes on Exports 3 and 4),
  `README.md`, `AUTORESEARCH_MECHANICS.md` (costar_import entry), Makefile
  help text.
- **Unchanged**: `research.py`, `prepare.py`, `runner.py`, every
  human-owned file (`git diff --quiet 3898047` on prepare.py,
  parameters.json, sources.json, program.md, zoning_rules.json,
  building_prototypes.json, rules_sources.json, requirements.txt,
  screener/rank.py, screener/autoresearch/data and research.py).

## Metric and run state (BC-04, BC-16, BC-17)

- research.py reads neither table's new rows (static tests), and its diff
  is empty. The four existing golden checks report identical (zoning
  ba4a780, site_fit 28d3205, hazards 8ddaf69, adjacency 595aa08).
- Live differential (own database copy of the local corridor, own market,
  3 parcels, one of them stored under another area than it resolves to):
  scored, imported 5 building and 6 lease comps, scored again: every
  parcel_scores column apart from score_id, scored_at, run_tag and
  experiment_id equal; `submarkets.bbox` and notes unchanged.
- Memo and snapshot with no tool rows: identical to the 3898047 record
  through `generate_strategy_memo` / `generate_snapshot`; the only new
  statements are the two building-comps reads at the end.
- The import is refused (exit 3) after the run's baseline for every sheet
  type: memo-only data still changes memos between experiments.

## Live numbers (counts only; synthetic rows)

- `tests/live_building_comps_check.py`: 16 of 16 checks pass on a
  throwaway copy of the local test database (created from `landtest`,
  dropped afterwards); re-run after round 1, with (b) and (c) updated for
  finding 3 (the shared sale is kept as a CSV row and a tool row; sale
  $/SF median over 6 tool sales, cap median over 5).
- End to end in a throwaway clone (CLI `python costar_import.py` on a
  generated two-sheet workbook, then `generate_strategy_memo('atlanta')`
  on the same throwaway database): 6 of 7 sales kept (1 sale-leaseback
  dropped; 1 blank cap rate), 7 of 8 leases kept (1 modified gross
  dropped), all assigned to 1 area; exit 0; both snapshot files written.
  The memo gained the section with 10 highlight lines; 4 highlights got an
  exit-value line (their projections fit; the other 6 have no fitted
  projection), all on market-wide medians because the highlights' area
  held no comp.
- No real CoStar building or lease export exists yet; no licensed row was
  used anywhere.

## What a human will notice

- `make costar-import` accepts building Sale Comps and Lease Comps sheets.
  Its console gains `building:` and `lease:` lines (kept, dropped by
  reason, cap rates left out, assignment).
- If the existing area sheet carries every building column, its sales now
  also become building comps (the console says so). If it lacks some, the
  console names them; the area import is unchanged.
- Two sheets of one kind in one workbook are now refused (before, the first
  was taken silently).
- A sheet that looks like building or lease comps but matches no layout is
  named on the console with the columns it lacks ("building: NOT
  imported: ..."); the other sheets still import. Alone, it is refused
  (exit 1) with that list. Before, such a sheet was ignored without a word.
- A Land sheet with no qualifying sale no longer blocks building or lease
  sheets in the same workbook.
- The console counts comps a CSV also delivered; they are stored by the
  tool too, so the memo counts them.
- Leases in buildings older than 15 years (when the export gives `Year
  Built`) are dropped and counted; the memo note says so.
- The exit-value line now states its assumptions (stabilised, 100% leased,
  no vacancy or reserves).
- Memos and snapshots gain the building-comps lines only after such an
  import.

## What a human must do

1. In CoStar, build the two saved searches in `COSTAR_EXPORTS_README.md`
   ("Building sale comps and lease comps"), add the listed columns in the
   column picker (Latitude/Longitude included), and export them, between
   runs, before the next baseline.
2. Run `make costar-import FILE=...`. If it refuses, or prints
   "building: NOT imported" / "lease: NOT imported", with missing columns,
   either add them in the column picker or send the developer the exact
   header CoStar uses (the alias lists are assumptions until a real export
   is seen). Include `Year Built` on the lease search.
3. Read the drop counts: if most leases fall out as "service type not NNN"
   or most sales as a sale condition, the filters may need a human
   decision (they are module constants in `costar_import.py`, changed
   between runs through the tiered review).

## Not done / could not do

- No real building or lease export to validate the assumed headers.
- No scoring use (BTS or spec strategy fit could use the rent later; that
  is a Tier 2 scoring change with a new run and baseline).
- Finding 8 (baseline refusal checks only the TSV, not the database
  mirror; pre-existing) is left for a separate Tier 2 change.
- No stabilised-vacancy constant in the exit value: a human valuation
  choice; the assumption is stated instead.
