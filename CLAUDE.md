# CLAUDE.md — Contract Card

This repo implements the Karpathy AutoResearch pattern (canonical spec:
`AUTORESEARCH_MECHANICS.md`) for industrial land sourcing. Its integrity
depends on file mutability rules that hold in **every** session — including
quick Q&A. Read this card first; the tier table below tells you how much
more you need to read before acting.

## Scope note: `screener/`

`screener/` is a separate, simpler tool (free-data land screen + its own
AutoResearch loop). It shares no code, tables or files with the system
below. The invariants and orientation tiers on this card govern the root
system. Work inside `screener/` follows `screener/README.md`, and its loop
follows `screener/autoresearch/PROGRAM.md`. There, the agent edits only
`screener/rank.py` and never edits `scoring.py`, `screens.py`,
`settings.toml`, `autoresearch/evaluate.py`, `autoresearch/build_eval.py`
or `autoresearch/data/`. The CoStar rule below applies everywhere.

## Invariants (always in force, at every tier)

- **NEVER edit `prepare.py` or `parameters.json`** — the immutable metric
  layer. If the metric, gates, or thresholds seem wrong, that is a signal
  for the HUMAN to run the prepare-mutation protocol between runs
  (`AUTORESEARCH_MECHANICS.md` § "When Mutating prepare.py") — never
  something you fix in a session.
- **NEVER edit `sources.json`** — the human adds sources between
  experiments. Adding sources from agent code is a metric-manipulation
  vector.
- **NEVER edit `program.md`** — human-only strategic direction, at any
  tier, in any session type.
- **NEVER edit `zoning_rules.json`** — human-owned zoning facts (per
  jurisdiction `draft`/`verified`, per district use status with
  citations). `zoning.py` freezes it by SHA-256 at load, like
  `parameters.json`: edits (above all draft → verified) are between runs
  only and need a new run + baseline (`reviews/21_zoning/`).
- **NEVER edit `building_prototypes.json`** — the operator's building
  prototypes (copied from their SiteFit tool). `site_fit.py` freezes it by
  SHA-256 at load and the runner refuses an experiment while it differs
  from git HEAD. Edits are between runs only; once any zoning jurisdiction
  is verified, an edit (or a `site_fit.py` change) moves BTS ratings and
  needs a new run + baseline (`reviews/22_site_fit/`).
- **NEVER edit `rules_sources.json`** — the human-owned source registry
  of the zoning-code scraper `rules_scrape.py` (between-runs operator
  tool). It is not a scoring input, but it decides which pages are read
  and fed to an extractor: `rules_scrape.py` freezes it by SHA-256 and
  the runner refuses to start an experiment while it differs from git
  HEAD (`reviews/23_rules_scraper/`). The scraper only ever writes a
  CANDIDATE under `rules_out/`; a human applies values to
  `zoning_rules.json` by hand, between runs.
- **Immutable during a run**: `runner.py`, `costar_ingest.py`,
  `costar_import.py`, `reporting.py`, `pipeline_common.py`, `zoning.py`,
  `zoning_import.py`, `site_fit.py`, `rules_scrape.py`, `hazards.py`,
  `hazards_import.py`, `adjacency.py`, `adjacency_import.py`. Between
  runs, changes there are Tier 2 (`STANDING_RISKS.md` § "Change tiers").
- **Flood/wetlands (H4/H6)**: the facts, SQL and decision live in
  `hazards.py`; `hazards_import.py` (between runs only) writes the
  proposed `parcel_hazards` table. Both stay inert until a human applies
  the prepare-mutation proposal in `reviews/26_flood_wetlands/`; the first
  populated import changes the metric and needs a new run + baseline.
- **Adjacency buffers (site fit)**: neighbour facts, the buffer-key
  allow-list and the per-side plan live in `adjacency.py`;
  `adjacency_import.py` (between runs only) writes the proposed
  `parcel_adjacency` table from the zoning layers in `sources.json`. Inert
  until a human applies the prepare-mutation proposal in
  `reviews/27_site_fit_buffers/`; residential buffers further need a
  human-written `neighbour_classes` table in `zoning_rules.json`. Once any
  jurisdiction is verified, enabling the table plus a populated import
  changes BTS and needs a new run + baseline.
- **`research.py` is the agent sandbox** — the ONLY file the agent edits
  during an experiment run. One focused change per experiment, committed
  as `exp: <description>` on the `autoresearch/<tag>` branch;
  keep-or-revert per `AUTORESEARCH_MECHANICS.md`.
- **`experiment_log.tsv` is append-only** and untracked. Never overwrite,
  never commit.
- **NEVER scrape CoStar** — legal risk; manual scheduled exports only
  (`COSTAR_INGESTION_CONTRACT.md`).
- **Real credentials only in `.env`** (gitignored). `env.template` carries
  placeholders only — enforced by pre-commit hook + CI after a real
  incident (see `reviews/02_setup_phase/00_setup_status.md`).
- Once an experiment loop is confirmed and running: **NEVER STOP** to ask
  permission. Halt only via `make halt`, explicit human instruction, or
  catastrophic infrastructure failure.

## How much orientation does this session need?

Orientation is tiered by blast radius, mirroring the review tiers
(`STANDING_RISKS.md`), so a quick question doesn't pay the same cost as an
experiment run.

| This session will… | Required orientation |
|--------------------|----------------------|
| Only read, inspect, diagnose, or answer questions; or edit docs (`.md`) — except `program.md` (never editable) and the canonical spec docs below | **Light**: this card → `README.md` § Status → `make status` (offline fallback: `make orient` — no DB needed). Confirm by stating the session type, the invariants you are bound by, and the current branch/run state. Then work. |
| Touch ANY `.py`/`.json` file, tests, or CI; run setup, an experiment, or the loop; or edit `AUTORESEARCH_MECHANICS.md` / `STANDING_RISKS.md` / `START_HERE.md` (the canonical spec, the risk register, and the orientation chain itself) | **Full**: the 6-step orientation chain in `START_HERE.md`, completed before acting. Not optional — skipping it silently corrupts the system. |

**Escalation rule**: if a light session drifts into full-tier territory
(you're asked to touch code, config, or the loop), STOP and complete the
full chain in `START_HERE.md` before acting on it.

Code changes are reviewed by blast radius — `STANDING_RISKS.md` § "Change
tiers": Tier 0 (tests + CI), Tier 1 (one independent fresh-context
reviewer), Tier 2 (full three-agent adversarial workflow).

## Operators (humans)

`make help` shows every target; `README.md` § "Quick Start" covers
first-run setup. Day-to-day: `make daily` → `make tail` → `make halt`.

## Developer setup (one-time per clone)

Run once after cloning the repo:

```bash
git config core.hooksPath .githooks
```

This activates the pre-commit hook at `.githooks/pre-commit` that blocks
commits which would put real credentials into `env.template`. The
server-side equivalent runs as a GitHub Actions workflow at
`.github/workflows/check-env-template.yml` and catches commits made via the
GitHub web UI or by clones without the local hook configured. See
`reviews/02_setup_phase/00_setup_status.md` "Incident" section for the
backstory.

Real credentials only ever go in `.env` (gitignored). The CI workflow at
`.github/workflows/validate-phase1.yml` re-runs `python prepare.py` against
a throwaway PostGIS service container on every push that touches
`prepare.py`, `parameters.json`, `requirements.txt`, or
`STORAGE_ARCHITECTURE.md`. It needs no secrets. The live Supabase project
is checked by `make verify` / `make status` from a Codespace with the real
`DATABASE_URL`.
