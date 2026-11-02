# 02 — Idempotent data_gap flags: decision note

> Item 2 of `00_plan.md`. **Tier 1** (a between-runs `research.py` logic
> change). Reviewed by one independent reviewer in a fresh context that
> did not write the code. Model names are omitted, as in
> `reviews/18_test_readiness/`.

## Change

`research.py` writes a parcel's `data_gap` flag only when no **open**
`data_gap` flag with the same parcel and description already exists.

- **Read once per cycle.** At cycle start, `run_discovery_cycle` (by
  market) and `run_scoring_cycle` (by parcel ids) each read the open gap
  keys once into a set. `flagged_items` has no `parcel_id` index, and its
  DDL belongs to `prepare.py`, so checking with `NOT EXISTS` on every
  insert would scan the table about 19k times per experiment.
- **Key.** `pipeline_common._flag` stores `cycle=<id>; <description>`, so
  the key strips everything up to the first `'; '`.
- **Rollback safety.** Keys enter the set only after the parcel's
  transaction succeeds, so a rolled-back insert is never treated as
  present.
- **Covered writers.** Every parcel-level writer: H3–H10, multi-polygon,
  null sub-scores, staleness, S6 and S8.
- **Unchanged.** Cycle-level flags (parcel_id NULL) still append, because
  they are events.
- **Re-opening.** A resolved or dismissed flag does not count, so a gap
  that is still there re-opens once.
- **Staleness text.** The staleness flag now reads `market_context older
  than 30d (... as_of=...)` instead of `stale by Nd`. Otherwise the same
  stale row would open a new flag every day.

## Evidence

**Live**, on local PostGIS 16/3.4, with the same test CoStar data and
1,075 live Fulton parcels per clone:

| Step | `flagged_items`, old code | `flagged_items`, new code |
|------|---------------------------|---------------------------|
| baseline | 16,695 | 16,695 |
| no-op experiment 1 (discard) | 25,314 | 16,695 |
| no-op experiment 2 (discard) | 33,933 | 16,695 |
| no-op experiment 3 (discard) | 42,552 | 16,695 |
| second run's baseline (re-scores all) | 59,246 | 16,695 |

**Metric and scores unchanged.**
- Metric 15 and confidence 8.75 at every step for both codes.
- `parcel_scores` stayed at 1,075 after each discard, so the purge
  behaves the same.
- The latest score row of every parcel in both runs (2,150 rows: composite,
  confidence, actionability, blockers, sub-scores, strategy fit, primary
  strategy, notes) is **byte-identical** between old and new code.

**Corrections to the plan's numbers.**
- A no-op experiment adds about **8 rows per discovered parcel**: the
  discovery stubs, 8,619 for 1,075 parcels.
- The roughly 10 scoring flags per parcel come only when a parcel is
  scored, i.e. a new run's baseline or a newly discovered parcel. Within a
  run, experiments do not re-score parcels the run has already scored
  (`01_*` point 6).
- So the old growth was about 8.6k rows per experiment. At about 16
  experiments a day that is about 140k rows a day, plus about 17k per new
  run.

**`research_log` still grows** by about 1,076 rows per experiment (one
discovery row per parcel plus ingestion). It is append-only history
(SR-13), so it is left alone. About 17k rows a day is the next growth
item to decide on for size-capped databases.

**Tests.**
- New `tests/test_data_gap_flags.py`, 9 offline tests:
  - skip, write and None semantics;
  - the description round-trip against `_flag`'s prefix;
  - a second scoring writes nothing, and scores are equal;
  - only the missing flag is written;
  - the staleness text is stable from day to day;
  - the registry set is used and cleaned up even on error;
  - a failed transaction does not mark keys present.
- `test_postgis_smoke.py` step 4b runs the stripping SQL on real
  PostGIS: re-discovery adds no row, and a resolved gap re-opens once.
- `test_discovery.py::test_two_feature_happy_path` now asserts exactly 16
  inserts (2 unique parcels × 8, since both corridors return the same two
  features) plus one gap read. The reviewer judged this stricter, not
  weaker.
- Two fakes gained the `fetchall()` read.
- Root suite: 945 OK. Screener: 134 OK.

## Review (independent, fresh context)

**Verdict: approve.**

**Verified:**
- the key is exact: cycle ids never contain `'; '`, and odd descriptions
  were checked on a live DB;
- legacy rows without the prefix fail open;
- every parcel-level writer is covered;
- the registry is cleaned up in `finally` on all paths;
- the metric, gates, purge and actionability are untouched;
- SR-2/5/6/9/13/14/15/16 hold.

**Performance on the reviewer's probe** (160k data_gap rows over 19k
parcels): the market read took 0.48 s and the `ANY(19k)` read 0.35–0.57 s,
once per cycle.

| # | Finding | Resolution |
|---|---------|------------|
| 1 | low: in an ad-hoc `score_parcel` (no registered cycle), the fallback per-parcel read was outside the per-parcel `try`, so a failed read raised instead of returning `status=error` | Moved inside the `try`. |
| 2 | low: visible behaviour changes | Recorded below. |
| 3 | low: the staleness wording is acceptable. Legacy `stale by Nd` rows never match, so each stale parcel gets one extra row, once. The S8 text includes `median_n`, so it re-opens when the comp count changes (bounded). | Accepted. |
| 4 | nit: the code comment cited this file before it existed | This note. |
| 5 | nit: two concurrent processes can still both insert (there is no unique constraint, and the DDL is human-only) | Accepted: bounded, and no worse than before. |

## Behaviour a human will notice

- **First sighting.** A flag's `cycle=` prefix and `flagged_at` now mean
  "first cycle that saw the gap". That cycle may belong to a discarded
  experiment; flags were never purged.
- **Snapshot "Flags / Open Items"** shows each open gap once, so real
  `actionability_block` flags are no longer pushed out of its 25 rows.
- **Memo "recent flags (7 days)"** stops re-listing a gap that has
  persisted for more than a week, and now shows the cycle-level and
  actionability flags that duplicates used to crowd out.
- **Existing duplicates are not deleted** (SR-13). An operator who wants
  to shrink an already-bloated database decides that separately.
