# Land Site Selector

Autonomous research agent for industrial real estate land sourcing.
Adapted from the [Karpathy AutoResearch](https://github.com/karpathy/autoresearch) paradigm.

## What This Does

This repository contains the specifications and (eventually) implementation for an autonomous agent that continuously discovers, screens, scores, and ranks land parcels for industrial real estate investment across target markets. The agent runs overnight, evaluates parcels against a configurable parameter stack, surfaces actionable opportunities, and produces investment thesis writeups for each qualified site.

The agent supports five investment strategies (BTS development, spec development, land banking, ground lease, land flip) and tags each qualifying parcel with the strategies it best fits. The team decides which strategy to pursue.

## Status

Phases 1–10 of `BUILD_PHASES.md` are shipped. The autonomous experiment loop runs end-to-end against Fulton County, with the full Karpathy-pattern infrastructure: setup-phase verifier, evaluator, append-only TSV experiment log, keep-or-revert decision logic, halt sentinel, advisory locking. Atlanta is the only configured market; Phase 11+ adds the remaining counties.

2026-07-07 restructure (see `reviews/14_streamlining_review/`):
- `research.py` was split — the experiment loop now lives in `runner.py`, CoStar ETL in `costar_ingest.py`, rendering in `reporting.py` (all immutable during a run); `research.py` holds only the agent's experiment surface.
- The metric is **run-scoped** (`prepare-mutation`): `parcel_scores` rows carry `run_tag` + `experiment_id`, the metric counts only the active run's rows, and the runner purges a discarded experiment's rows so the data ratchet mirrors the git ratchet. Pre-mutation metric values are not comparable to post-mutation values; the next run starts fresh.
- The review process is **tiered** (`STANDING_RISKS.md`): tests+CI for low-risk changes, one independent fresh-context reviewer for ordinary logic, the full three-agent workflow for metric/contract-touching work.

For live repo state, run `make status` (setup checks + last TSV rows) — prose snapshots of state in documents go stale; the command does not.

## Quick Start (Operator)

This codebase is operated through `make`. Open the repo in a Codespace and run:

```bash
make daily              # ONE COMMAND: cuts/resumes today's autoresearch branch,
                        # verifies Supabase, kicks the loop in a detached tmux
                        # session, prints attach + tail commands.

# In a second terminal:
make tail               # live-stream experiment_log.tsv as rows land
make status             # verify_setup + last 10 TSV rows
make db-stats           # per-table row counts (parcels, parcel_scores, etc.)

# To stop the loop cleanly:
make halt               # creates .halt sentinel; loop exits on next iteration

# To reattach to the running loop:
make loop-attach        # tmux attach -t loop  (Ctrl-B d to detach again)
```

**One-time setup per Codespace** (the devcontainer handles this for new Codespaces):

1. Add `DATABASE_URL` to your User-level Codespaces secrets at https://github.com/settings/codespaces — Supabase Session pooler DSN, format `postgresql://postgres.<ref>:<URL_ENCODED_PASSWORD>@aws-<n>-<region>.pooler.supabase.com:5432/postgres`. Grant the secret access to this repo.
2. Open the Codespace. The devcontainer's `post-start.sh` materializes `.env` from the secret automatically.
3. Verify: `make db-check` should print PostGIS version + `actionable_pipeline_count: 0`.

**Karpathy iteration loop** (what the agent does between `make loop` runs):

1. Read `experiment_log.tsv`. The most recent `baseline` or `keep` row is the prior anchor.
2. Form a hypothesis. Edit `research.py` (the only file the agent edits). One focused change.
3. `git commit -m "exp: <description>"` on the active `autoresearch/<tag>` branch.
4. `make loop MAX=1` — runs one full cycle, appends a row to `experiment_log.tsv` with the keep-or-revert decision.
5. Read the new row. If `status=keep`, branch advances. If `status=discard`, `git reset --hard HEAD~1`. If `status=crash` or `timeout`, diagnose and retry.

Per `AUTORESEARCH_MECHANICS.md` "The Experiment Loop", the agent (Claude Code) drives the iteration; `make loop` provides the evaluator + decision recording. Auto-revert is intentionally NOT in `make loop` — that's the agent's responsibility (R-723 / D1).

`make help` shows every available target with descriptions.

## Repository Structure

```
land-site-selector/
├── CLAUDE.md                          — Contract card: invariants + orientation tiers (agents read FIRST)
├── START_HERE.md                      — Full orientation chain (required before code/loop work)
├── README.md                          — This file
├── AUTORESEARCH_MECHANICS.md          — CANONICAL: how the Karpathy pattern is implemented
├── program.md                         — The agent's autonomous loop instructions
├── appendix_a_county_connectors.md    — County data source specs, tiered review workflow, harness design
├── STANDING_RISKS.md                  — Recurring risk checklist + review tiers (cited by ID in reviews)
├── COSTAR_INGESTION_CONTRACT.md       — How CoStar exports feed the agent
├── COSTAR_EXPORTS_README.md           — Operator guide for the CoStar saved-search setup
├── STORAGE_ARCHITECTURE.md            — Postgres + PostGIS schema decisions
├── BUILD_PHASES.md                    — Implementation roadmap
├── parameters.json                    — Scoring weights and filter thresholds (IMMUTABLE during run)
├── sources.json                       — Registry of data source URLs (LOCKED — agent never writes)
├── connector_registry.json            — Harness-only config overlay (test bboxes, expected extents)
├── prepare.py                         — IMMUTABLE: metric calculation, DDL, frozen parameters
├── research.py                        — Agent sandbox — the ONLY file the agent modifies in a run
├── runner.py                          — IMMUTABLE during run: experiment loop, setup checks, TSV I/O
├── costar_ingest.py                   — IMMUTABLE during run: CoStar export ETL
├── reporting.py                       — Snapshot + strategy memo rendering
├── pipeline_common.py                 — Shared paths/helpers/SQL for the pipeline modules
├── connector_harness.py               — Connector validation framework
├── cli.py                             — Operator CLI (argparse; --json output)
├── Makefile                           — Operator targets (make help)
├── data/                              — Bundled reference data (OZ tract stub, etc.)
├── tests/                             — Offline suite (600 tests, ~1s) + fixtures
├── reviews/                           — Review artifacts per change (decision notes; historical 3-agent docs)
├── .devcontainer/                     — Codespaces config: secret -> .env hydration
├── .githooks/ + .github/workflows/    — Credential guard, offline+live CI, harness CI
├── experiment_log.tsv                 — AutoResearch experiment log (UNTRACKED, runtime-only)
└── snapshots/ rankings/ harness_reports/ sources/
                                       — Generated runtime artifacts (gitignored, created on demand)
```

## For Claude Code (and any AI coding agent)

**Read `CLAUDE.md` first** — a one-screen contract card with the always-on file mutability invariants and a tier table telling you how much orientation the session needs. Read-only, diagnostic, and docs-only sessions proceed on the card alone (light orientation). Any session touching code, config, tests, the canonical spec, or the experiment loop must then complete the 6-step chain in `START_HERE.md` before acting — skipping it silently corrupts the experimental log.

## For Humans

`CLAUDE.md` is the one-screen version of the rules everything below justifies — start there. Then read for your need; new to the repo entirely, read the table top to bottom (it doubles as the onboarding order):

| If you want to… | Read |
|-----------------|------|
| Operate the loop day-to-day | This README § Quick Start, then `make help` |
| Understand how the Karpathy pattern is implemented — canonical, wins all conflicts | `AUTORESEARCH_MECHANICS.md` |
| See what the agent optimizes, how parcels are scored, and redirect it between runs | `program.md` |
| Add a county, fix a connector, or read the coding workflow spec | `appendix_a_county_connectors.md` |
| Review a code change (change tiers + recurring risk checklist) | `STANDING_RISKS.md` |
| Understand the database schema and storage decisions | `STORAGE_ARCHITECTURE.md` |
| Set up or debug CoStar exports | `COSTAR_EXPORTS_README.md` (ops) + `COSTAR_INGESTION_CONTRACT.md` (spec) |
| See the roadmap and how each phase was built and reviewed | `BUILD_PHASES.md` + `reviews/` |

## The Tiered Review Workflow

Code changes are reviewed by blast radius (full definition:
`STANDING_RISKS.md` § "Change tiers"; process spec:
`appendix_a_county_connectors.md` → "Coding Workflow"):

- **Tier 0** — docs, config, stubs, ops tooling: tests + CI only.
- **Tier 1** — ordinary `research.py` logic: ONE independent fresh-context reviewer + a short decision note.
- **Tier 2** — metric layer, runner decision logic, harness internals, credentials, external integrations: the full three-agent adversarial workflow (risk reviewer → code writer → reviewer-implementer with sole commit authority), each role in a genuinely separate context on the strongest available model.

The recurring risk checklist all tiers verify against lives in `STANDING_RISKS.md` (SR-1 … SR-15) — reviews cite IDs instead of restating it.

## Operating Cost (Estimated)

| Component | Cost |
|-----------|------|
| Infrastructure (Supabase free tier or DigitalOcean droplet) | $0–25/month |
| Claude API usage (absorbed by Max subscription) | $0/month |
| Data subscriptions (CoStar already in firm's stack, county APIs free) | $0/month |
| **Total** | **$0–25/month** |

## Target Markets

- **Tier 1**: Atlanta, Dallas-Fort Worth, Houston, Chicago
- **Tier 2 (Sun Belt secondaries)**: Nashville, Charlotte, Raleigh-Durham, Jacksonville, San Antonio, Columbus OH, Memphis
- **Future expansion**: Orlando, Lehigh Valley PA (NJ blocked by Daniel's Law without third-party data subscription)

Initial build targets Atlanta only.

## Key Decisions Made

- **Storage**: Postgres + PostGIS (Supabase free tier acceptable for initial build)
- **Coding workflow**: Tiered review (`STANDING_RISKS.md`); three-agent team at Tier 2, strongest available model
- **Data approach**: API-first (Approach 3) with AI-assisted fallback (Approach 2)
- **CoStar**: Manual scheduled export ingestion, no scraping (legal risk)
- **Primary metric**: `actionable_pipeline_count` (not just qualified — must pass actionability screen)
- **Strategy tagging**: Every qualifying parcel tagged with which of the 5 strategies it fits
- **Investment thesis**: Required for every actionable parcel, written in narrative form
- **Strategy memo**: Generated per market per cycle, explains agent's thought process

## License

Internal use only.
