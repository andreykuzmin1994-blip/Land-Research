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
post-change adversarial review that follows this stage), each in its own
fresh context. The pre-change register BC-01 to BC-20 came from a context
that did not write the code. The post-change adversarial review is the
next workflow stage; it has not run yet.

## Verdict

| Step | Who | Result |
|---|---|---|
| Pre-change risk register (BC-01 to BC-20) | independent reviewer, fresh context | 20 items: 2 blocking, 7 high, 9 should-fix, 2 nits; every one disposed (one by design: no new module) |
| Implementation | implementer | memo only; no scoring input; byte-identical memo and snapshot without tool rows |
| Post-change adversarial review | next stage | not yet run |

**Ship as memo-only.** Nothing changes for an operator who has not imported
a building or lease export.

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
- **Tests**: `tests/test_building_comps.py` (67), golden case + check +
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
  dropped afterwards).
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
- Memos and snapshots gain the building-comps lines only after such an
  import.

## What a human must do

1. In CoStar, build the two saved searches in `COSTAR_EXPORTS_README.md`
   ("Building sale comps and lease comps"), add the listed columns in the
   column picker (Latitude/Longitude included), and export them, between
   runs, before the next baseline.
2. Run `make costar-import FILE=...`. If it refuses with missing columns,
   either add them or send the developer the exact header CoStar uses
   (the alias lists are assumptions until a real export is seen).
3. Read the drop counts: if most leases fall out as "service type not NNN"
   or most sales as a sale condition, the filters may need a human
   decision (they are module constants in `costar_import.py`, changed
   between runs through the tiered review).

## Not done / could not do

- No real building or lease export to validate the assumed headers.
- No scoring use (BTS or spec strategy fit could use the rent later; that
  is a Tier 2 scoring change with a new run and baseline).
- The post-change adversarial review has not run yet.
