# Standing Risks — the recurring checklist

> Canonical home for the risk themes that apply to **every** code change in
> this repo. Reviews cite these by ID (e.g. "SR-2: enforced by the AST
> scanner, nothing new here") instead of re-deriving them. Anything
> phase-specific still gets written out in full in the change's review.
>
> Origin: the process audit in `reviews/14_streamlining_review/` measured
> that ~40–55% of every per-phase risk review restated the items below.
> They were real risks — they just never changed between phases.

## How to use this file

- **Authors**: before writing code, skim the list; design against it.
- **Reviewers**: verify each applicable item, then record ONLY deviations
  and phase-specific findings. "All standing risks hold; no new SQL
  surface" is a complete standing-risk section for a small change.
- **Enforcement**: many items are enforced mechanically by the test suite
  (noted inline). Do not hand-verify what a test already proves — cite the
  test.

## The standing risks

| ID | Risk | Rule | Mechanical enforcement |
|----|------|------|------------------------|
| SR-1 | Five-file-contract violation | No pipeline code writes `program.md`, `parameters.json`, or `sources.json`. The agent edits ONLY `research.py` during a run; `prepare.py`/`runner.py`/`costar_ingest.py`/`reporting.py`/`pipeline_common.py` are immutable during a run. | `TestStaticChecks.test_no_immutable_writes`, `TestStrictNoImmutableWrites`, and the G2/G6/G11 class scan ALL pipeline modules |
| SR-2 | SQL injection / dynamic SQL | All SQL is module-level constants with `%s` placeholders; `cursor.execute` first arg must be a Name/Attribute/Constant, never an f-string. | `test_no_string_interpolated_sql` (whole-surface AST scan), per-phase constant checks |
| SR-3 | Parameter-freeze bypass | Read tunables via `prepare.get_parameters()` only; call `prepare.verify_parameters_unchanged()` at cycle entry. Never re-parse `parameters.json`. | SHA-256 sentinel in `prepare.py`; routing asserted in cycle tests |
| SR-4 | Cycle-id collision / replay | Every cycle generates a fresh unique id and aborts if rows for that id already exist. | Collision-guard tests per cycle type |
| SR-5 | Transaction boundaries | One parcel/file per transaction; rollback on row error must not poison the connection; explicit final commit on shared connections. | Per-cycle transaction tests |
| SR-6 | Offline test hermeticity | Every new code path must be testable with `FakeConnection`/fixtures, no network, no `DATABASE_URL`. Live behavior belongs in the CI service-container jobs. | The suite itself (must stay green offline) |
| SR-7 | Path traversal / filename safety | Anything derived from external data that becomes a filename goes through a slug/safe-path helper; writes are atomic (`os.replace`). | Slug + atomic-write tests |
| SR-8 | PII / redaction split | Owner names are stored verbatim in Postgres (canonical record) but REDACTED in any report artifact (`harness_reports/`). Never log credentials; DSNs go through `prepare._mask_dsn`. | Harness redaction tests; masked-DSN tests |
| SR-9 | Idempotency | Re-running any cycle must not duplicate rows: UPSERT for parcels, DELETE-then-INSERT for re-ingests, append-only for scores/logs/TSV. | Idempotency tests per loader/cycle |
| SR-10 | Dependency creep | No new third-party dependency without an explicit human decision (`requirements.txt` is 3 lines; keep it that way). | Reviewer eyeball on `requirements.txt` diff |
| SR-11 | Polite scraping | 1 request/second per county host, bounded retries with backoff, Retry-After capped, harness gate before production queries. County servers are not built for volume. | `_DiscoverySession` + harness rate-limit tests |
| SR-12 | Secrets hygiene | Real credentials ONLY in `.env` (gitignored). `env.template` carries placeholders — enforced after the Phase 1 leak incident. | `.githooks/pre-commit` + `check-env-template.yml` CI |
| SR-13 | Append-only experiment history | `experiment_log.tsv` and `parcel_scores` are append-only from the pipeline's perspective; the ONLY sanctioned deletion is `runner._purge_experiment_scores` reverting a non-kept experiment's own rows by `experiment_id`. | TSV writer tests; purge tests (`TestExperimentPurge`) |
| SR-14 | `action_type` vocabulary | New `research_log.action_type` values must be added to the enum list in `program.md` in the SAME change (the column is TEXT; the doc is the contract). | Reviewer check — history shows this drifts (Phase 3 `abort`, Phase 6 `ingestion`) |
| SR-15 | Metric-integrity boundary | Nothing outside `prepare.py` computes or filters the metric; `research.py` produces INPUTS only. Any change to what counts (columns, scoping, purge rules) is a `prepare-mutation:` commit with a fresh run + baseline. | Metric routing tests; `prepare-mutation` protocol |

## Change tiers (which review a change needs)

Defined here so the appendix, README, and START_HERE can all point at one
place. Judged by blast radius, informed by 13 phases of measured outcomes
(reviews/14_streamlining_review/ §Finding C: genuinely independent review
caught real bugs; same-context ceremony caught none).

- **Tier 0 — tests + CI only.** Docs, config values, stubs that return
  constants, ops tooling (Makefile/cli.py), test-only changes. No review
  document. The standing risks still apply; the suite enforces them.
- **Tier 1 — one independent reviewer.** Ordinary `research.py` logic
  changes and additions (new sub-score logic, discovery heuristics, new
  county connector wiring). One reviewer in a FRESH context (a subagent or
  a second session that did not write the code) reads the diff against
  this checklist and the change's stated intent, and records a short
  decision note (a PR review or ≤1 page in `reviews/`): what was checked,
  what was found, what changed. No 3-document ceremony.
- **Tier 2 — full adversarial review.** Anything touching `prepare.py`,
  `runner.py`'s decision/purge/TSV logic, the metric or its scoping,
  credentials/auth, external-service integration, or the mutability
  contract itself. Independent risk enumeration BEFORE the change plus an
  adversarial fresh-context review AFTER it, both by contexts that did not
  author the code. Use the strongest available model for these roles and
  record which model ran in the decision note. The classic three-agent
  workflow (appendix A) is a valid Tier 2 implementation; the requirement
  is CONTEXT INDEPENDENCE, not document count.

When in doubt between tiers, pick the higher one; when a sub-agent is
unavailable (quota, tooling), say so in the decision note rather than
silently self-reviewing — the audit showed self-review approval means
little.
