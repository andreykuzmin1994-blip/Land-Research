# Proposed program.md Fixes — For the Human to Apply

> `program.md` is human-only at every tier; the agent never edits it.
> These are paste-ready replacements aligning its six remaining
> file-based-pipeline references with the shipped Postgres
> implementation (storage: `STORAGE_ARCHITECTURE.md`; git discipline:
> `AUTORESEARCH_MECHANICS.md` + program.md's own Constraint 9). Line
> numbers refer to program.md v1.2 (2026-07-07). Apply, adjust, or
> reject — operator's call.

## 1. Line 156 — Hard Filters intro (`rejected/{parcel_id}.json`)

**Current:**
> Every discovered parcel runs through these filters first. Failure on ANY hard filter → immediate rejection. The agent records the rejection reason in `rejected/{parcel_id}.json` and moves on.

**Proposed:**
> Every discovered parcel runs through these filters first. Failure on ANY hard filter → immediate rejection. The agent records the rejection as a `research_log` row (`action_type='rejection'`, failing filter in `notes`) and moves on.

## 2. Line 234 — Discovery output (`markets/{market_id}/candidates.json`)

**Current:**
> For each discovered parcel, create an entry in `markets/{market_id}/candidates.json`:

**Proposed:**
> For each discovered parcel, UPSERT a row into the `parcels` table (schema: `STORAGE_ARCHITECTURE.md`). The logical record is:

(The JSON example that follows stays — it documents the logical shape,
which maps onto `parcels` columns.)

## 3. Line 274 — Scoring sequence step 2

**Current:**
> 2. If ANY hard filter fails → write to `rejected/` with reason → update candidates.json → next parcel

**Proposed:**
> 2. If ANY hard filter fails → record a `research_log` rejection row with the reason → next parcel

## 4. Line 282 — Scoring sequence step 10

**Current:**
> 10. Write full scored profile to `scored/{parcel_id}.json`

**Proposed:**
> 10. Write the full scored profile as a `parcel_scores` row (sub-scores, strategy fit, and actionability blockers in JSONB; narrative in `investment_thesis`)

## 5. Lines 284–285 — Scoring sequence steps 12–13 (the important one)

**Current:**
> 12. Update `rankings/{market_id}_ranked.json` — rank actionable parcels first, then qualified_not_actionable
> 13. Git commit: `git add . && git commit -m "SCORE: {market} | {parcel_id} | {composite_score}/100 | {actionability} | {primary_strategy} | actionable_pipeline: {actionable_pipeline_count}"`

**Proposed:**
> 12. Rankings are derived from Postgres (actionable parcels first, then qualified_not_actionable) and rendered by `reporting.py` into `rankings/`
> 13. Record the action as a `research_log` row (`action_type='scoring'`). Git history is per-EXPERIMENT, not per-parcel: exactly one `exp: <description>` commit per experiment — see Constraint 9 and `AUTORESEARCH_MECHANICS.md` "The Experiment Loop"

Step 13 as written mandates a git commit per scored parcel, which
directly contradicts Constraint 9 ("ONE commit per experiment … The
per-action record is the `research_log` table, not git history") and the
canonical mechanics. An agent following the loop text literally would
take the wrong side of the contradiction; this is the highest-value fix
in the file.

## 6. Line 349 — Spec-development fit analysis (`market_context.json`)

**Current (fragment):**
> Use submarket asking rents from market_context.json and rough industrial construction cost estimates

**Proposed (fragment):**
> Use submarket asking rents from the `market_context` table and rough industrial construction cost estimates

## Optional bookkeeping

If applied, bump the version line in "Notes for Human Iterating on This
File" (e.g., to 1.3: "file-based pipeline references aligned with the
Postgres implementation; per-parcel commit contradiction with Constraint
9 resolved").
