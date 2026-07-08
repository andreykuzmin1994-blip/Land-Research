# Tier-2 Decision Note — experiment_log.tsv Postgres durability mirror

> Decision-note format per `STANDING_RISKS.md` § Change tiers (deltas
> only). Closes gate G8 of `00_risk_review.md`.

- **Change under review**: commit `796379d`
  (`prepare-mutation: add experiment_log_mirror durability table — metric
  UNCHANGED`), merged to main via PR #25 (`ef7fa57`) at the operator's
  explicit instruction ("Merge please") while the adversarial review was
  in flight — the reviews/14 precedent, declared in the commit message.
  Follow-up fixes: the commit carrying this note.
- **Roles and context independence**: risk reviewer (Agent 1) and
  adversarial reviewer (Agent 3) each ran in a fresh subagent context
  that did not author the code; the author context implemented against
  Agent 1's gates and never self-reviewed. All three roles ran on the
  session-strongest model (Claude Fable 5). Agent 3 verified the merged
  content byte-identical to the diff it reviewed.
- **Verdicts**: risk review — PROCEED-WITH-CHANGES (all conditions
  implemented). Adversarial review — **APPROVE-WITH-FIXES**; no
  REJECT-class defect ("no input, failure injection, or invocation path
  by which the mirror raises into the loop, precedes the TSV, feeds a
  decision, deletes history, or corrupts a restored log"). Gates G1–G7,
  G9 PASS; G8 PARTIAL → closed by this note.

## Metric-comparability carve-out (G8 — read this before analyzing the TSV)

This `prepare-mutation` is **DDL plus a non-exiting DSN probe only**. The
metric definition, actionability gates, run scoping, purge SQL, anchor
selection, and `parameters.json` are byte-unchanged — verified
independently by both reviewers against the diff. **TSV metric values
remain comparable across commit `796379d`.** Do NOT apply
`AUTORESEARCH_MECHANICS.md` § "When Mutating prepare.py" step 6's
"not comparable" rule to this mutation; it exists for metric-definition
changes, which this is not. The next run starts a fresh tag/branch/
baseline as always.

## Findings disposition (F-numbers from `01_adversarial_review.md`)

| # | Disposition |
|---|-------------|
| F1 (MED, kill-switch empty-string hole) | **FIXED** in this commit: `tests/__init__.py` coerces a falsy (unset OR empty) `EXPERIMENT_LOG_MIRROR_DISABLE` to `"1"`; new `TestSuiteKillSwitch` asserts the switch is truthy at suite runtime, catching both the empty-string override and a missing package `__init__`. The PYTHONPATH-direct-file bypass remains theoretically possible but requires an invocation no documented path uses. |
| F2 (MED process, artifacts missing) | **FIXED** in this commit: `01_adversarial_review.md` + this note landed. Agent 1's "zero autoresearch branches" claim is corrected for the record: origin holds a stale, fully-merged `autoresearch/atl-2026-05-05`; the between-runs conclusion stands. |
| F3 (LOW, prod DDL not applied — dead DSN secret) | **OPERATOR ACTION, OPEN**: `validate-phase1` live-validate fails on a pre-existing dead `DATABASE_URL` repository secret (fix in flight on `claude/verify-validate-secret`). Until a green live run (or any `make db-check`/`make daily`) executes `apply_schema` against prod, `experiment_log_mirror` does not exist there and every live mirror write warn-fails — the durability feature is INERT. After fixing the secret, confirm live-validate green, then run `make mirror-backfill` once a TSV exists. `verify_setup` mirror-table existence check earmarked as optional hardening. |
| F4 (LOW, statement_timeout comment overclaim) | **FIXED** in this commit (comment reworded: bounds lock-waits/slow statements; a true partition is TCP-bounded — the same accepted exposure class as every pre-existing DB call in the loop). |
| F5 (LOW, benign `mirror_only` explanations) | **DOCUMENTED here** (accepted): before reading `mirror_only > 0` as tampering (SR-16 canary), rule out the two benign causes — (a) a live append whose mirror insert landed between backfill's TSV snapshot and its COUNT (window: milliseconds around an append; consequence: one duplicate mirror row, never a decision input); (b) rows a restore skipped as invalid remaining mirror-only. |
| F6 (LOW, static guards are evadable regexes) | **ACCEPTED** as tripwires against accidental regressions (consistent with the repo's other static guards); deliberate evasion is SR-16 territory, and backfill heals a forgotten mirror site. |
| F7 (LOW, restore error message remedy) | **FIXED** in this commit (message now names file deletion for the hand-created header-only case). |

## Standing risks

SR-2/SR-5/SR-6/SR-8/SR-9/SR-15 verified by both reviewers (module-level
`%s` SQL constants; single-transaction backfill behind an advisory lock;
suite hermetic with zero network; masked-DSN-only logging; count-based
idempotent reconciliation; metric reads pinned to `parcel_scores`).
SR-13 and SR-16 text updated in `796379d` itself. SR-10 clean (no new
dependencies).

## Residual state

- The TSV remains canonical; the mirror is never a decision input.
- Durability becomes REAL only after F3's operator action (dead DSN
  secret → live `apply_schema`). Until then it is warn-only inert.
- Routine ops: run `make mirror-backfill` occasionally while the TSV is
  alive (idempotent; doubles as the SR-16 divergence canary);
  `make mirror-restore` only when the TSV is gone.
