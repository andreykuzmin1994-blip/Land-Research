# Decision Note — Usability Pass: Tiered Orientation + Contract Card

> Tier 0 (docs only) per `STANDING_RISKS.md` § "Change tiers" — tests + CI,
> no review document required. This note exists anyway because the
> orientation chain is process-load-bearing and future sessions should find
> the rationale where they expect it (`reviews/`).

- **Trigger**: operator request, 2026-07-07 — "Can we make this easier to
  use?" (branch `claude/usability-improvement-ictc5k`).
- **Session type**: Specification refinement (docs-only usability pass).
  Orientation chain Steps 1–4 completed in full before acting.

## The problem

Every Claude Code session paid the same orientation cost regardless of
blast radius: ~3,500 lines across 7 documents plus a mandatory hard STOP at
Step 5 — even for "answer a question about the Makefile." Meanwhile
`CLAUDE.md`, the one file every session loads automatically, carried no
rules at all (it was a bare redirect to `START_HERE.md`), so a session that
skipped orientation knew *nothing* rather than *the essentials*. The
Step 5 STOP also guaranteed a wasted round-trip on asynchronous sessions
(remote/mobile) whose initial message already stated the goal unambiguously
— including the very session that produced this change.

This mirrors the pre-2026-07-07 review process: uniform heavyweight
ceremony, where the audit (`reviews/14_streamlining_review/` § Finding C)
showed the protection comes from matching process weight to blast radius,
not from applying maximum weight everywhere.

## What changed

1. **`CLAUDE.md` is now a contract card**: the always-on invariants
   (never edit `prepare.py`/`parameters.json`/`sources.json`/`program.md`;
   run-immutable modules; `research.py` as the only sandbox; append-only
   TSV; no CoStar scraping; credentials in `.env` only; NEVER STOP) plus a
   two-row tier table. This also satisfies the `AUTORESEARCH_MECHANICS.md`
   implementation-checklist item "the agent's startup prompt in CLAUDE.md
   explicitly tells it not to modify prepare.py or parameters.json"
   directly instead of by pointer.
2. **`START_HERE.md` gained Step 0** (tier routing): light orientation
   (card + README Status + `make status`, one-paragraph confirmation) for
   read-only/diagnostic/docs sessions; the full Steps 1–6 chain, unchanged,
   for anything touching `.py`/`.json`/tests/CI, the canonical spec docs,
   setup, experiments, or the loop. Explicit escalation rule for sessions
   that drift. Editing the chain itself (`START_HERE.md`) is full-tier —
   a lightly-oriented session must not rewrite the gate it skipped.
3. **Step 5's STOP is now scoped**: mutation-capable session types (setup,
   build, experiment, loop) still hard-stop for human confirmation; the
   read-only/docs-only types proceed when the initial message is
   unambiguous, and still stop on any ambiguity.
4. **`README.md`** cross-references updated (agent entry point is the card;
   tree annotations; humans pointed at the card first). Also fixed a Step 5
   internal contradiction ("Skip Step 6" on a session type that Step 6
   defines a branch for).

## What did NOT change

- The five-file contract and every mutability rule — restated, not relaxed.
- The full 6-step chain for any session that can corrupt the system.
- The NEVER STOP rule, the setup phase, keep-or-revert, the review tiers.
- Zero `.py`/`.json`/test/CI changes.

## Verification

- Offline suite green after the change (`make tests`), and grep confirms no
  test or workflow references the edited docs.
- No test/CI coupling to `CLAUDE.md`, `START_HERE.md`, or `README.md`
  content existed before or after.
