# Land Site Selector

Autonomous research agent for industrial real estate land sourcing.
Adapted from the [Karpathy AutoResearch](https://github.com/karpathy/autoresearch) paradigm.

## What This Does

This repository contains the specifications and (eventually) implementation for an autonomous agent that continuously discovers, screens, scores, and ranks land parcels for industrial real estate investment across target markets. The agent runs overnight, evaluates parcels against a configurable parameter stack, surfaces actionable opportunities, and produces investment thesis writeups for each qualified site.

The agent supports five investment strategies (BTS development, spec development, land banking, ground lease, land flip) and tags each qualifying parcel with the strategies it best fits. The team decides which strategy to pursue.

## Status

This is a **specification repository**. The `.md` files define what the agent should do, how it should be structured, and how the data sources should be connected. Implementation has not started.

## Repository Structure

```
land-site-selector/
├── START_HERE.md                      — Claude Code orientation chain (READ FIRST if you are an agent)
├── CLAUDE.md                          — Pointer to START_HERE.md
├── README.md                          — This file
├── AUTORESEARCH_MECHANICS.md          — CANONICAL: how the Karpathy pattern is implemented
├── program.md                         — The agent's autonomous loop instructions
├── appendix_a_county_connectors.md    — County data source specifications and harness design
├── COSTAR_INGESTION_CONTRACT.md       — How CoStar exports feed the agent
├── STORAGE_ARCHITECTURE.md            — Postgres + PostGIS schema decisions
├── BUILD_PHASES.md                    — Implementation roadmap
├── parameters.json                    — Scoring weights and filter thresholds (IMMUTABLE during run)
├── sources.json                       — Registry of data source URLs (locked during run)
├── prepare.py                         — IMMUTABLE: metric calculation and evaluation (TO BE BUILT)
├── research.py                        — Agent sandbox — only file the agent modifies (TO BE BUILT)
├── connector_harness.py               — Connector validation framework (TO BE BUILT)
├── experiment_log.tsv                 — AutoResearch experiment log (UNTRACKED, runtime-only)
├── markets/                           — Per-market discovered candidates and context
├── rankings/                          — Current ranked shortlists per market
├── snapshots/                         — One-page parcel snapshots for human review
├── harness_reports/                   — Connector health reports
├── sources/                           — Cached raw API responses per parcel
├── flagged/                           — Items requiring human review
└── docs/
    └── diligence_program.md           — Companion: post-LOI diligence agent spec (separate scope)
```

## For Claude Code (and any AI coding agent)

**Read `START_HERE.md` first.** It walks you through a 6-step orientation chain with explicit confirmation gates. Do not skip it — this repo implements the Karpathy AutoResearch pattern and skipping orientation silently corrupts the experimental log.

## For Humans

Read in this order:

1. `README.md` (this file)
2. `AUTORESEARCH_MECHANICS.md` — Canonical specification of how the pattern is implemented. If anything else in this repo conflicts with this document, this document wins.
3. `program.md` — The agent's strategic instructions
4. `appendix_a_county_connectors.md` — Data source specs, three-agent coding workflow, connector harness
5. `STORAGE_ARCHITECTURE.md` — Postgres + PostGIS schema
6. `COSTAR_INGESTION_CONTRACT.md` — CoStar workflow (no scraping)
7. `BUILD_PHASES.md` — Implementation roadmap

## The Three-Agent Coding Workflow

All production code in this project must be developed using a three-agent code team running Claude Opus 4.7:

- **Agent 1: Risk and Architecture Reviewer** — Surfaces failure modes and architectural concerns before code is written
- **Agent 2: Code Writer** — Writes code that addresses every risk Agent 1 identified
- **Agent 3: Reviewer and Implementer** — Critically analyzes both prior outputs, has sole commit authority

See `appendix_a_county_connectors.md` → "Coding Workflow: Three-Agent Code Team" for the full specification.

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
- **Coding workflow**: Three-agent code team with Opus 4.7
- **Data approach**: API-first (Approach 3) with AI-assisted fallback (Approach 2)
- **CoStar**: Manual scheduled export ingestion, no scraping (legal risk)
- **Primary metric**: `actionable_pipeline_count` (not just qualified — must pass actionability screen)
- **Strategy tagging**: Every qualifying parcel tagged with which of the 5 strategies it fits
- **Investment thesis**: Required for every actionable parcel, written in narrative form
- **Strategy memo**: Generated per market per cycle, explains agent's thought process

## License

Internal use only.
