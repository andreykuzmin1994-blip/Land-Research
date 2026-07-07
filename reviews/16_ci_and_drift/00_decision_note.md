# Decision Note — Unconditional Offline CI + Drift Fixes

> Tier 0 per `STANDING_RISKS.md` § "Change tiers" (CI config, ops-tooling
> docstring, docs, `.gitignore`). No behavior of the pipeline changes.
> Follow-on to `reviews/15_usability_pass/` (operator: "what else can be
> improved" → "proceed"). Full orientation chain was completed in the
> authoring session.

## What changed

1. **`.github/workflows/offline-tests.yml` (new)** — runs the full
   offline suite (~1s, no secrets) on EVERY push and PR, unconditionally.
   Before this, all workflows were path-filtered and collectively left
   gaps: `cli.py` appeared in no filter at all, and a `runner.py` change
   ran only `tests.test_discovery`. Tier 0's definition ("tests + CI
   only") assumes CI always runs the tests; now it does.
2. **`AUTORESEARCH_MECHANICS.md`** — the crash-handling section said
   dependencies come from `pyproject.toml`; the repo pins them in
   `requirements.txt` (which SR-10 already names). One factual line; no
   semantic change to the pattern. Canonical-doc edit made under full
   orientation.
3. **`cli.py` docstring** — said it wraps "research.py's public API";
   post-split the loop/baseline/status entry points live in `runner.py`.
   Docstring only.
4. **`.gitignore`** — pruned the pre-Postgres file-based design's dead
   entries (`markets/*` tree, `flagged/*.json`) and added a comment
   naming the real on-disk runtime outputs. Verified by grep: no code
   writes those paths. `results.tsv` kept as a habit-safety net.
5. **`STORAGE_ARCHITECTURE.md`** — `harness_reports` table marked as a
   future-locked extension point: the DDL ships but nothing writes it
   (the harness persists JSON files + the markdown dashboard). Verified
   by grep: no `INSERT INTO harness_reports` anywhere.
6. **README** — dropped the stale "600 tests" count (suite is at 614 and
   moving; prose snapshots of state go stale — the repo's own rule).

## Proposed, NOT applied (human-only file)

`program.md` still specifies the retired file-based pipeline in six
places, including a per-parcel `SCORE:` git commit that contradicts its
own Constraint 9 (one `exp:` commit per experiment). The agent must not
edit `program.md` at any tier — paste-ready replacement wording is in
`01_program_md_proposed_fixes.md` for the operator to apply (or reject).

## Deferred pending operator decision

- **`experiment_log.tsv` durability** — canonical cross-run history is an
  untracked file in an ephemeral Codespace; recommended fix is an
  append-only Postgres mirror table (Tier 2 + prepare-mutation; between
  runs — no `autoresearch/*` branches exist right now, so the window is
  open).
- **Overnight runs vs Codespaces idle timeout** — detached tmux does not
  keep a Codespace alive past its idle stop (30 min default, 4 h max);
  either document a keep-alive/host recommendation or move the loop host
  to a durable machine.

## Verification

- 614 offline tests pass after the change.
- `make orient`, `make help` still work (Makefile untouched this pass).
- No test or workflow references the edited docs (checked before edit).
