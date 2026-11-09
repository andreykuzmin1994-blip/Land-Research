# 03 — Flood and wetlands: decision note

> **Tier 2.** Design `00_design.md`; storage proposal for the human
> `01_prepare_mutation_proposal.md`; pre-change register, dispositions and
> post-change review findings `02_risk_review.md`. This note records the
> verdict, what the reviewers verified, the evidence, the behaviour a human
> will notice, and the exact operator steps. Model names are omitted.

## Verdict

The independent adversarial review (two reviewers and a skeptic, none of
whom wrote the code) confirmed twelve findings: two high, five
should-fix, five nits. All twelve are fixed and pinned by a test. Finding 7
is kept as the literal reading and handed to the human as an open
question. No finding is open.

| # | Sev | Finding (short) | Verdict |
|---|---|---|---|
| 1 | high | H4/H6 enforcement only in the sandbox once enabled | fixed: `runner.evaluate` re-checks counted parcels outside the sandbox; prepare-side SQL option for the human; SR-16 residual recorded |
| 2 | should-fix | Storage gate switchable from research.py in process | fixed: gate read from prepare.py's source text, frozen at load; static ban on any `prepare.<attr>` write in research.py |
| 3 | should-fix | `AE_with_LOMR` relaxed on any LOMR touching the parcel | fixed together with 4 |
| 4 | high | Corner LOMR off the AE strip turned an AE FAIL into a pass | fixed: AE relaxes only when the LOMR union covers the parcel's whole AE area; new proposed column `nfhl_zone_lomr_sf` |
| 5 | should-fix | Area-of-use test on the raw (invalid) centroid | fixed: made-valid centroid and envelope; empty repaired geometry counted apart |
| 6 | should-fix | "Not imported" printed after a committed source | fixed: committed sources named; exit 2 documented as possibly partial |
| 7 | nit | AH/AO/A99/AR pass with a review flag | kept (literal `flood_zones_blocked`); open question for the human, below |
| 8 | nit | Wrong parameter-order comment in site fit | fixed (16 placeholders, pinned) |
| 9 | nit | Projection text claimed deductions the parcel lacks | fixed: per-source presence and the no-determination part in the text |
| 10 | nit | NWI state package with several .shp fails files mode | fixed: the single `*_Wetlands.shp` is chosen |
| 11 | nit | LOMR hit per subdivided piece, not the union | fixed: union before area |
| 12 | nit | dBASE deleted flag ignored; uncovered parcels silent | fixed: deleted records skipped; uncovered previously imported parcels counted |

## What the reviewers verified

- **Finding 1** reproduces from the code. prepare's metric SQL counts
  `parcel_scores` rows research.py writes. `grep -i hazard runner.py`
  returned nothing at the time, runner.py never runs the test suite, and
  the substring test on `score_parcel` is defeated by a second assignment.
  The skeptic could not refute it. Mitigations (nothing changes until a
  human enables the table; H1-H3 share the exposure) do not cover the state
  after enablement.
- **Finding 2**: the skeptic re-ran the reviewer's probe on an in-memory
  copy of research.py. Static guards gave `[]`, the constant regex matched
  nothing, `enabled()` was True and `schema_problem()` None. No repo file
  was edited.
- **Findings 3/4**: the skeptic traced the `lm` CTE (whole-parcel
  intersection) into `hazards._flood`. The reviewer's own PostGIS probe (a
  1,000 x 1,000 ft parcel with a 200 ft west AE strip and a 100 x 100 ft
  NE-corner LOMR) gave H4 review with no blocker, and blocked without the
  LOMR. No live case covered "LOMR on the parcel, off the AE".
- **Finding 5**: reproduced on local PostGIS 16 with a central-Atlanta
  bowtie. The raw centroid was about 1.3 degrees west, outside the area of
  use; the made-valid one was inside. On the corridor copy, 0 parcels were
  excluded.
- **Finding 6**: traced through `run()`. The NFHL commit is durable before
  the NWI failure re-raises into the outer handler.
- **Findings 7-12**: confirmed by reading the code and the probes listed in
  `02_risk_review.md`. Finding 10 could not be checked against the live
  USFWS package, because the download hosts are blocked by robots policy.

## Change in this round

- `runner.py` (protected; Tier 2): `_hazard_fingerprint`, `_hazard_audit`
  and `HazardAuditFailed`; `evaluate` calls them only while
  `hazards.enabled()`. It imports `hazards`, which imports only `prepare`.
- `hazards.py`: `_SOURCE_DDL` / `ddl_from_prepare_source` (gate from
  prepare.py's source text); `nfhl_zone_lomr_sf` in the proposed DDL, the
  columns and the read; the LOMR-coverage rule; `deduction_text` presence
  flags.
- `hazards_import.py`: `lu`/`lm`/`pz`/`pl` CTEs (LOMR union, per-zone LOMR
  areas); made-valid list and box SQL; empty repaired geometry counted
  apart; partial-commit message; previously imported parcels not covered
  counted; deleted dBASE records skipped; `*_Wetlands.shp` choice.
- `site_fit.py`: the hazard SQL returns `has_sg`, `has_wg` and `nfhl_gap`
  (16 parameters, comment corrected), and the projection text uses them.
- Tests: `tests/test_hazards_runner.py` (new), `static_guards.
  prepare_write_problems` (new), and additions to `test_hazards`,
  `test_hazards_scoring`, `test_hazards_import` and `live_hazards_check`
  (cases (c), (j) and (k)).
- Docs: `00_design.md` (gate, H4 LOMR reading, metric-side enforcement,
  computation, writes), `01_prepare_mutation_proposal.md` (new column,
  gate wording, optional metric SQL), `02_risk_review.md` (round-1 table),
  AUTORESEARCH_MECHANICS.md section 3 (the overclaim corrected), the SR-16
  row, and README.

research.py is not edited in this round. prepare.py, parameters.json,
sources.json, program.md, requirements.txt, zoning_rules.json,
building_prototypes.json, rules_sources.json, screener/rank.py and
screener/autoresearch/data/ are byte-identical to 8ddaf69 (pinned by
`test_hazards_scoring.ProtectionTests`).

## Decisions (unchanged from the implementation stage unless marked)

1. **Storage is a proposal; the gate costs no SQL.** *Round 1:* the gate is
   now read from prepare.py's source text, parsed once when `hazards`
   loads. A runtime assignment to `prepare._DDL_PARCEL_HAZARDS` changes
   nothing. While the constant is absent, every consumer is byte-identical
   to 8ddaf69 and the import refuses with exit 1.
2. **H4 = any area of a blocked zone at or above a 1 SF numeric noise
   floor blocks; a floodway always blocks.** No sliver percentage was
   invented.
3. **`AE_with_LOMR` (round 1, metric-relevant, for the human to confirm):**
   AE (outside any floodway) passes with a review flag only when the union
   of the LOMR polygons covers the parcel's whole AE area: the AE inside
   the LOMR is at least 1 SF and the AE outside it is under 1 SF. A LOMR
   elsewhere on the lot relaxes nothing. A partial cover blocks, and the
   blocker names it. The strictest reading, "AE always blocks", is one
   parameters.json edit away: remove the token from
   `flood_zones_flag_for_review`, between runs. LOMA is never checked.
4. Other SFHA zones, future conditions, levee and open water are review
   flags. Unmapped areas, Zone D and AREA NOT INCLUDED are data gaps. The
   0.2% zone passes.
5. **H6** fails only when the share is strictly above
   `wetlands_max_pct_of_parcel`, compared exactly. A smaller positive share
   is a review pass ("minimum footprint undefined").
6. **Enforcement:** gate 4 at scoring (research.py). *Round 1:* once
   enabled, the protected `runner.evaluate` re-checks every counted parcel
   through `hazards.py` and discards an experiment that counted an H4/H6
   failure, read an unreadable row, or saw `parcel_hazards` change.
7. **Three states, stale by geometry md5.** Absent, stale and unreadable
   are data_gap passes.
8. **Site fit** deducts the SFHA (floodway included) and NWI wetlands. It
   does not deduct the 0.2% zone. *Round 1:* the text says "no SFHA on
   parcel" or "no NWI wetlands on parcel" when nothing was deducted, and
   names the part with no FEMA determination. BTS still moves only for
   verified jurisdictions.
9. **Data route:** robots.txt is honoured unchanged, and local files are
   the default.
10. **The first populated `make hazards-import` is the intended metric
    change.** It needs a new run and baseline.

## Open question for the human (finding 7)

program.md defines H4 as the "100-year floodplain" and names Zone A and
AE. `flood_zones_blocked` is `["A","AE"]`, so AH, AO, A99, AR, V and VE
(all 1% annual-chance SFHA, `SFHA_TF = 'T'`) pass with the review flag
"SFHA zone X on parcel (not in flood_zones_blocked): flag for review". That
is the literal reading and it is lenient. Options, between runs only:

- (a) extend `flood_zones_blocked` in parameters.json (for example
  `["A","AE","AH","AO","A99","AR"]`); no code change; or
- (b) treat every `SFHA_TF = 'T'` polygon as blocked; this is a code change
  in `hazards.py` plus a parameters.json key.

How many corridor parcels this affects: **not measurable yet.** No real
NFHL data could be imported (see LIVE). The first operator import prints
it as part of "H4 outcomes: ... review N".

## Evidence

- Offline suite: `python -m unittest discover -s tests`: 1,494 tests, OK
  (1,359 at 8ddaf69; 1,464 before this round; 30 new in this round).
  Screener suite: `python -m unittest discover -s screener/tests -t .`: 134
  tests, OK.
- Golden checks, all exit 0 and all unmodified:
  `tests/zoning_golden_check.py`, `tests/site_fit_golden_check.py`,
  `tests/hazards_golden_check.py`. research.py is not edited in this round,
  and the gate is off in the shipped repo, so every parcel_scores field,
  flag and statement still matches the 8ddaf69 record.
- `tests/test_hazards_runner.DisabledTests`: with the gate off,
  `runner.evaluate` issues no hazard statement and opens no extra
  connection.
- `tests/live_hazards_check.py` (own database, created and dropped, DDL
  applied there only): all checks OK, including the new ones:
  - (c) AE wholly inside a LOMR gives review. The finding's probe (200 ft
    west AE strip, NE-corner LOMR) blocks; that row has
    `lomr_status = intersects`, no AE key in `nfhl_zone_lomr_sf` and
    10,000 SF of X inside the LOMR. A LOMR over half the AE blocks with
    "a LOMR covers only part of zone AE".
  - (j) the bowtie parcel's raw centroid is at lon -89.968 (outside the
    area of use) and its made-valid centroid at -84.483. The parcel is
    imported (NFHL fresh), with "0 outside EPSG:2240's area of use; 0 with
    no polygonal area after repair".
  - (k) on real SQL, a forged PASS row for the floodway parcel makes
    `_hazard_audit` raise "1 counted parcel(s) carry an H4/H6 blocker". A
    clear parcel passes. A one-row `parcel_hazards` edit between the
    fingerprints raises "changed during the experiment".
  - (g) projection text: "SFHA incl. floodway deducted (0.2% zone not
    deducted); no NWI wetlands on parcel; fill/compensatory storage not
    considered". Buildable area goes from 2,102,500 to 1,661,700 SF, and
    every envelope is disjoint from the strip.
  - Earlier cases (a)-(i) unchanged.

  `tests/live_site_fit_check.py` and `tests/live_zoning_check.py` still
  pass.
- The optional prepare-side metric SQL in the proposal was composed with
  prepare's own CTE and filter and executed once on a throwaway PostGIS
  database with the DDL applied (result 0 on empty tables; database
  dropped).

## LIVE (counts only)

Own copies of the 1,075 corridor parcels (`createdb -T landtest`;
`landtest` itself untouched), plus a scratchpad clone with prepare.py
patched as the proposal says. Both copies (hz_rv26, hz_rv26b) and the
clone were dropped or removed.

- **Geometry:** 1,075 Fulton parcels, 57 invalid ("Hole lies outside
  shell"). Parcels outside EPSG:2240's area of use: 0 by the raw centroid
  and 0 by the made-valid centroid. Empty after repair: 0.
- **Import, files mode, DDL applied, no downloaded files:** "1075 Fulton
  parcels with geometry; 0 outside EPSG:2240's area of use; 0 with no
  polygonal area after repair". Both sources FAILED (files not present),
  exit 4. H4/H6 outcomes: gap 1,075 each.
- **Runner audit on the corridor copy (present, empty table), for each of
  the five run tags in its parcel_scores:** metric 15. The audit counted
  15, blocked 0, stale 0, absent 15. It reads the same universe as the
  metric.
- **Real hazard counts are still not available.** Parcels with any SFHA,
  floodway, 0.2% zone or wetlands, the share distribution, H4/H6 fail
  counts and the change in site-fit projections are not measured. The NFHL
  county package and the NWI state shapefile are browser-only downloads,
  and both REST hosts' robots.txt deny the queries (stage 1 evidence,
  unchanged). Nothing was fabricated or substituted. These are the numbers
  the first operator `make hazards-import` prints.

## Behaviour a human will notice

Before the DDL is applied: none. Every score, flag, memo line and
statement is as at 8ddaf69, and `make hazards-import` refuses with exit 1.

After the DDL, with an empty table: only flag text changes (two "no
hazards import" data_gaps per parcel). `runner.evaluate` now fingerprints
`parcel_hazards` before and after each experiment and re-assesses the
counted parcels. Every counted parcel is "absent", so nothing is
discarded.

After a populated import (the metric change):

- AE/A parcels fail through gate 4.
- AE parcels relax to a review pass only where a LOMR covers all of their
  AE. Expect fewer relaxed parcels than the first version would have given.
- Any experiment whose research.py lets an H4/H6 failure count shows up in
  the TSV as `crash` with error `HazardAuditFailed: hazard audit: N counted
  parcel(s) carry an H4/H6 blocker`, and it is discarded. The same happens
  if `parcel_hazards` changes mid-experiment or cannot be read. An
  unreadable table makes every experiment crash, the baseline included,
  until it is fixed.
- The import's console lines now also show "N with no polygonal area after
  repair" and, per source, "N previously imported parcels not covered
  (previous values kept)".
- A DB failure after a committed source says which source committed.
- Projections say "no SFHA on parcel" / "no NWI wetlands on parcel" where
  nothing was deducted, and "part of the parcel has no FEMA flood
  determination (not deducted)" where that applies.

## Operator steps (between runs only, in this order)

1. Decide the two human questions first: the `AE_with_LOMR` reading
   (decision 3: keep the token, or remove it so AE always blocks), and the
   other SFHA zones (finding 7: keep, or extend `flood_zones_blocked`). Any
   parameters.json edit follows the prepare-mutation protocol.
2. **DONE 2026-11-09 by the agent under the operator's delegation, without
   the optional exclusion (see "Operator decisions (2026-11-09, item 4)"
   below; reviews/33_hazards_enabled).** Apply
   `01_prepare_mutation_proposal.md` to prepare.py: the
   `_DDL_PARCEL_HAZARDS` string literal at module level (the gate reads the
   file's text) and the index tuple, both in `_ALL_DDL`. Mirror the DDL in
   STORAGE_ARCHITECTURE.md. Optionally add the prepare-side metric
   exclusion from the same document. Commit as `prepare-mutation: ...`.
3. Run `make db-check` (runs `python prepare.py`, creates `parcel_hazards`).
4. In a browser, download the FEMA NFHL county package for Fulton (13121C)
   and the USFWS NWI Georgia shapefile. Unpack them into
   `hazards_inbox/nfhl/` (`S_FLD_HAZ_AR.*`, plus `S_LOMR.*` if present) and
   `hazards_inbox/nwi/` (the `*_Wetlands.shp` set; extra project-metadata
   shapefiles may stay).
5. Run `make hazards-import`. Check exit 0. Read the counts: parcels with
   SFHA, floodway, 0.2% zone and wetlands; share bands; H4/H6 outcomes;
   "previously imported parcels not covered". Exit 4 names the failed
   source and keeps its old data. Exit 2 after "source(s) ... committed"
   means a partial import: re-run before the baseline.
6. Start a new run (AUTORESEARCH_MECHANICS.md "The Setup Phase") and record its baseline. Never
   import mid-run: the tool refuses with exit 3 once the run has a
   baseline, and the runner audit discards an experiment if the table
   changes.
7. Optionally resolve the old open "H4 flood unjoined" / "H6 wetlands
   unjoined" stub flags (they stay open).

## Not done / for the human

- The two questions in operator step 1.
- The real-data import (steps 4-5). Applying the proposal (step 2) was
  done 2026-11-09 under the operator's delegation (reviews/33_hazards_enabled).
  The real hazard counts listed under LIVE come from that import.
- Whether boundary slivers need a parameters.json tolerance, after seeing
  the real SFHA bands.

## Operator decisions (2026-11-09)

Recorded in `reviews/29_operator_decisions/` (decision D2): **"Flag, don't
block"**. Finding 7 ("Open question for the human") is **answered**: keep the
current behaviour. No code change; `hazards.py` and `hazards_import.py`'s
flood logic are unchanged and `parameters.json` stays as the human wrote it
(`flood_zones_blocked` `["A","AE"]`, `flood_zones_flag_for_review`
`["AE_with_LOMR"]`).

- Zone AE wholly covered by a LOMR passes H4 with a review flag (a LOMR
  covering only part of the AE area still blocks).
- The other 1% annual-chance SFHA zones pass H4 with a review flag: AH, AO,
  A99, AR, and also **V and VE**, which the code treats the same way although
  the operator's answer named only AH/AO/A99/AR. The 1% future-conditions
  zone, the levee-reduced-risk zone and open water are review flags too.
- Review flags never change actionability. Pins:
  `tests/test_hazards.py` `test_ae_with_lomr_is_a_review_pass`,
  `test_other_sfha_zones_are_reviews_not_blocks` (AH, AO, A99, AR, V, VE),
  `test_future_conditions_levee_open_water_are_reviews`;
  `tests/test_hazards_scoring.py` `test_review_flags_never_change_actionability`;
  `tests/test_operator_decisions.py`
  `PinTests.test_flood_thresholds_are_the_human_values` (read-only check of
  the two parameters.json keys).
- If the operator later wants V/VE blocked, that is option (a) above
  (extend `flood_zones_blocked`, human edit between runs, new run and
  baseline).

## Operator decisions (2026-11-09, item 4): table enabled by the agent

The operator's chat message of 2026-11-09, verbatim, as relayed by the
workflow harness:

> 1 - Count them, jsut review, 3 - fix 4 - can you add this yourself. Start
> building what you proposed keeping my answers in mind

Item 4 of the recommended sequence was "Operator prepare-mutation for
hazards (reviews/26)". "can you add this yourself" delegates that one edit
to the agent, acting for the human between runs. The delegation covers the
two constants and the `_ALL_DDL` entries of `01_prepare_mutation_proposal.md`,
one provenance comment, and the SQL mirror in STORAGE_ARCHITECTURE.md. It
does not cover the optional metric-SQL exclusion (not applied), any other
prepare.py change, or any human-owned file. The other answers are carried
out elsewhere and do not change this folder's flood rules. "Count them" is
D3. "jsut review" is D2: V/VE stay review-only, a floodway still blocks, no
flood-rule change, and no new root sub-score, because the null-beats-low
question is not answered. "fix" covers the research.py and zoning_import
fixes (the ArcGIS error-envelope and audit-path bugs) and the D1-11
zoning_import fetcher round. "Start building what you proposed" (step 2
of that sequence) is the screener quick wins. Record, register, dispositions
and evidence: `reviews/33_hazards_enabled/`.

**Operator step 2 above is DONE, by the agent, under this delegation**
(commit subject `prepare-mutation: ...`, on the work branch; the
operator's merge to `main` is the protocol's commit-to-main step). Without
the optional exclusion. Step 1's two questions were answered on 2026-11-09
(D2, the section above). The operator steps that remain, between runs, in
this order:

1. Merge to `main` (halt any active loop first: `make halt`).
2. `make db-check` (`python prepare.py`) on the REAL database, BEFORE any
   `make verify`, `make baseline` or `make daily`. Otherwise every
   experiment, the baseline included, ends `crash` with
   `HazardAuditFailed: hazard audit: parcel_hazards unreadable (...)`.
   With the table empty, nothing is scored differently.
3. Browser downloads, unpacked FLAT:
   - into `hazards_inbox/nfhl/`, the FEMA NFHL Fulton package `13121C`:
     `S_FLD_HAZ_AR.shp`, `.dbf` and `.prj`, plus optional `S_LOMR.*`;
   - into `hazards_inbox/nwi/`, the USFWS NWI Georgia shapefile: one
     `.shp`, or one `*_Wetlands.shp`, with its `.dbf` and `.prj`. The `.prj`
     must be NAD83 or WGS84 geographic, or NAD83 Conus Albers.

   Details: `reviews/33_hazards_enabled/02_code_writer_response.md`.
4. `make hazards-import`. Exit codes: 0 written; 4 a source failed (its
   data kept); 2 after "committed" partial (re-run); 3 the run has its
   baseline (start a new run first); 1 refused (not merged, or no table).
5. A new run + baseline. The first populated import IS the metric change.
6. Optional: resolve the old open "H4 flood unjoined" / "H6 wetlands
   unjoined" flags.

What moved with the table empty, against the 8ddaf69 record: scores,
inserts and notes are identical for 22/22 parcels and the cycle.
Discovery H4/H6 changed `flag` -> `pass`, and the two stub flagged_items
INSERTs per parcel are gone. Scoring adds one hazard read per path and two
"no hazards import for this parcel" data_gap INSERTs per parcel. Tests that
predate the hazard read run under `tests/hazards_helpers.disabled()`: 25
rewiring uses, 32 in all, never suite-wide.

Merge order with the sibling 2026-11-09 changes (the D3 counting, the
research.py and zoning_import fixes, and the screener) does not matter.
Whichever lands second rebases, then re-runs both suites and the five
golden checks. A conflict must not drop a `disabled()` wrap or delete a
test.
