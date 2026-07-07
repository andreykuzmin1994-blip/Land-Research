# Tier-2 Review Decision — sandbox split + run-scoped metric

> Decision-note format per STANDING_RISKS.md § Change tiers (deltas only —
> the full adversarial report is summarized in the disposition table).

- **Change under review:** commits `1fc28b8` (research.py split) and
  `34b37ed` (prepare-mutation: run-scoped metric + purge). Both merged to
  main via PR #23 at the operator's explicit instruction before the review
  completed; fixes below landed as an immediate follow-up.
- **Reviewer:** independent fresh-context subagent (general-purpose,
  session-strongest model), pinned worktrees at both commits, ran the full
  suite at each, executed three behavioral probes. Author context and
  reviewer context were fully separate.
- **Verdict:** split — APPROVE. Metric change — APPROVE-WITH-FIXES.
- **Standing risks:** SR-1..SR-15 verified by the reviewer's clean list
  (SQL param orders, purge-by-id-only, DISTINCT ON determinism, no
  circular imports, patch-target semantics, DDL idempotency). New SR-16
  recorded as an accepted risk out of finding F3(a).

## Findings disposition

| # | Severity | Finding (compressed) | Disposition |
|---|----------|----------------------|-------------|
| F1 | HIGH | TSV accumulates across runs → run #2 never re-baselines, anchors against a non-comparable prior run, purge-livelocks honest work; confirmed-gate bypassed | **FIXED**: `run=<tag>` marker stamped into every loop/baseline row; `has_baseline` and `_last_baseline_or_keep` scoped to the current tag (unmarked legacy rows force a fresh baseline + re-arm the confirmation gate); loop stamps the marker authoritatively so even crash-fallback rows carry it |
| F2 | HIGH | `prepare.current_run_tag()` cwd-sensitive (wrong dir → silent unscoped metric or foreign repo's tag); loop relied on derivation despite docstring claiming explicit threading | **FIXED**: `cwd=_REPO_ROOT` pinned; `experiment_loop` hard-fails without a tag and passes `run_tag` explicitly to `evaluate` → scoring + metric; `run_baseline_experiment` derives cwd-pinned with a loud off-branch warning |
| F3a | HIGH | research.py can rebind `prepare.calculate_*` at runtime (same interpreter) — split removes file-level, not runtime, tampering | **ACCEPTED + documented** as SR-16 (requires a drifting agent; exp-commit diffs are human-reviewable); runner docstring claim corrected; subprocess-isolated metric read earmarked as the hardening if the audit ever fires |
| F3b | HIGH | Mis-stamped `experiment_id` lets a discarded experiment's rows survive the purge | **FIXED (monitor)**: per-experiment stamp audit in `evaluate` counts wrong-stamp rows written in the window; surfaced as `stamp_audit_violations` in the log and TSV description |
| F4 | MED | Orphaned rows from killed processes undiscoverable; purge one-shot | **FIXED (partial)**: `exp=<id>` stamped in every TSV description; failed purges queued and retried each iteration boundary. Full orphan reconciliation documented in AUTORESEARCH_MECHANICS as the operator recipe, not yet automated |
| F5 | MED | Timeout/crash livelock (purge → identical re-run forever); side tables grow unbounded | **FIXED (partial)**: 5-consecutive-crash/timeout breaker halts the loop; purge-scope limits (parcel_scores only; research_log/flagged_items/parcels not reverted) documented explicitly |
| F6 | MED | Suite fails when run from an `autoresearch/<tag>` checkout (one test missing the run-tag pin) | **FIXED**: `current_run_tag` pinned in `TestPhase8MetricEndToEnd` |
| F7 | LOW | On-branch ad-hoc scoring stamps run rows with NULL experiment_id (unpurgeable) | **FIXED (warn)**: `score_parcel` logs loudly when writing run-tagged rows without an experiment id |
| F8 | LOW | Loop lock is per-clone; two clones share a run_tag against shared Supabase | **Documented** in AUTORESEARCH_MECHANICS (operator responsibility; DB advisory lock keyed on run_tag is the candidate fix) |
| F9 | LOW | Stale 10-column comment; duplicated branch regex; unscoped reporting reads | **FIXED**: comment corrected; runner regex single-sourced from `prepare`; reporting's unscoped-by-design reads documented. db-stats hardcoded threshold left as-is (display-only) |

## Verification

- 614 offline tests pass (8 new regression tests: run-aware baseline/anchor
  + confirmation gate, cwd pinning, tag threading, purge retry, terminal
  breaker, stamp audit).
- The reviewer's probes (cross-run livelock, cwd sensitivity, runtime
  rebinding) are each addressed by a fix or an explicitly recorded
  acceptance above.

## Process observation

This review is the first run of the tiered process — and it validated the
premise it was built on: a genuinely independent fresh context found two
HIGH operational defects and one HIGH integrity gap that the authoring
context missed, in the exact category (metric-touching, Tier 2) the
process reserves for full adversarial review.
