# Build Phases

> Implementation roadmap for the Land Site Selector agent.
> Realistic time estimates assuming evening/weekend work and the tiered review workflow (STANDING_RISKS.md).
>
> **Progress note (2026-07-07): Phases 1-10 are SHIPPED** — see README.md "Status" and run `make status` for live state. The per-phase prompts below are kept as the historical build recipe and the template for Phase 11+.

---

## Prerequisites (Already Done)

✅ Specifications written: `program.md`, `appendix_a_county_connectors.md`, `COSTAR_INGESTION_CONTRACT.md`, `STORAGE_ARCHITECTURE.md`
✅ Fulton County ArcGIS endpoint validated (returns parcel data with owner names, acreage, assessed values)
✅ Three-agent coding workflow defined (mandatory for all production code)
✅ Storage decision: Postgres + PostGIS, Supabase free tier for initial build
✅ CoStar approach: manual scheduled exports (no scraping)

---

## Phase 0: Environment Setup (1 evening, ~2 hours)

**Goal**: A development environment ready to run code and connect to a database.

Tasks:
1. Set up local development environment with Python 3.10+, Node.js 18+, Git
2. Verify Claude Code is installed and authenticated against Max subscription (`claude --version`)
3. Create Supabase free-tier project, enable PostGIS extension
4. Configure `.env` with `DATABASE_URL` and `ANTHROPIC_API_KEY` (for fallback layer if needed beyond Max)
5. Clone this repo, verify all `.md` files are readable

**Exit criteria**: `claude` runs in the repo directory and can read `program.md`. Postgres connection from a test script returns the PostGIS version.

---

## Phase 1: Core Scaffolding (1 evening, ~2 hours)

**Goal**: Repository scaffolding and configuration files in place.

Use the three-agent workflow. Tell Claude Code:

> "Read program.md, appendix_a_county_connectors.md, and STORAGE_ARCHITECTURE.md. Run the three-agent coding workflow to produce: prepare.py (one-time setup script that creates all required tables in Postgres per the storage architecture), parameters.json (scoring weights and filter thresholds with defaults from program.md), sources.json (initial data source registry), and an empty research.py with the loop structure stubbed but no implementation. Do not start the experiment loop yet."

Tasks the three-agent team handles:
1. Risk reviewer flags concerns: schema validation, Postgres connection failure modes, parameter file validation
2. Code writer produces `prepare.py`, `parameters.json`, `sources.json`, scaffolded `research.py`
3. Reviewer-implementer validates and commits

**Exit criteria**: `python prepare.py` runs successfully, creates all tables in Supabase. `parameters.json` reflects program.md defaults and can be tuned.

---

## Phase 2: Connector Test Harness (1 evening, ~2-3 hours)

**Goal**: The harness exists and works against Fulton County before any individual connector is built.

Use the three-agent workflow. Tell Claude Code:

> "Read appendix_a_county_connectors.md, specifically the Connector Test Harness section. Run the three-agent coding workflow to build connector_harness.py per that specification. The harness must be operational against Fulton County (the only seeded connector at this point). Include the registry-based config, all 10 standard validation checks, JSON health reports, and the markets-wide dashboard generator. Do not build any other county connectors yet."

**Exit criteria**: `python connector_harness.py --county fulton` produces a healthy report. The Fulton field mapping from the validated API response is hardcoded into the connector registry. The harness catches synthetic failures (test by pointing it at a wrong URL).

---

## Phase 3: First County Connector + Discovery (1-2 evenings, ~3-4 hours)

**Goal**: The agent can discover and ingest Fulton County parcels into Postgres.

Use the three-agent workflow. Tell Claude Code:

> "Read appendix_a_county_connectors.md and program.md. Run the three-agent coding workflow to build the Fulton County discovery connector in research.py. It should: query the Fulton ArcGIS parcel layer within configured corridor bounding boxes (start with South Fulton and West Atlanta/I-20), filter by acreage 5-50, map results into the parcels table per STORAGE_ARCHITECTURE.md, run hard filters H1-H4 from program.md, write to flagged/rejected as appropriate, and log to research_log. Do not implement scoring yet."

**Exit criteria**: A discovery cycle runs against Fulton, produces real parcel records in Postgres, harness still passes after the run.

---

## Phase 4: Hard Filters Complete (1 evening, ~2 hours)

**Goal**: All 10 hard filters operational, including environmental and federal data sources.

Use the three-agent workflow. Add hard filters H5-H10 covering environmental contamination (EPA Envirofacts), wetlands (USGS NWI), road access, utility availability, topography (USGS 3DEP), and ownership availability.

**Exit criteria**: A discovery cycle filters parcels through all 10 hard filters. Rejected parcels have rejection reasons logged.

---

## Phase 5: Scoring Engine MVP (1-2 evenings, ~3-4 hours)

**Goal**: Composite scoring with the parameters that don't require CoStar data.

Use the three-agent workflow. Implement scored parameters S1 (interstate proximity via PostGIS distance), S2 (parcel geometry), S3 (topography from USGS), S7 (labor pool from Census LODES), S8 (land basis vs. county-level comps for now), S9 (entitlement complexity stub - returns moderate by default), S10 (incentives - check OZ map), S11 (rail adjacency), S12 (proximity to demand generators).

**Exit criteria**: Parcels in the database have composite_score values. Some parameters return null where data isn't yet available; this is expected.

---

## Phase 6: CoStar Ingestion (1 evening, ~2 hours)

**Goal**: CoStar export files feed the agent.

Pre-requisites for the user (one-time setup outside code):
- Configure 5 saved searches in CoStar with weekly/monthly email schedules per COSTAR_INGESTION_CONTRACT.md
- Set up email-to-folder pipeline (Gmail filter + Drive, Outlook + OneDrive, etc.)

Use the three-agent workflow. Implement the CoStar ingestion pipeline: scan ingestion folders, validate file schemas, load into market_context / sales_comps / leasing_comps / land_listings tables, archive processed files, log results.

**Exit criteria**: A CoStar export file dropped into the ingestion folder is loaded into Postgres within one agent cycle. Validation failures are flagged appropriately.

---

## Phase 7: Scoring Engine Complete (1 evening, ~2 hours)

**Goal**: All scored parameters operational, including the CoStar-dependent ones (S4, S5, S6, refined S8).

Use the three-agent workflow. Wire S4 (vacancy from market_context), S5 (absorption), S6 (pipeline), and refine S8 (land basis using sales_comps for actual submarket median pricing).

**Exit criteria**: Composite scores fully populated for parcels in submarkets where CoStar data is available.

---

## Phase 8: Actionability Screen + Strategy Fit (1-2 evenings, ~3-4 hours)

**Goal**: The pipeline metric (actionable_pipeline_count) is real, and parcels are tagged with strategy fit.

Use the three-agent workflow. Implement:
- The 4-gate actionability screen (path to control informational, plausible entitlement, viable strategy with next step, no deal-killers)
- Strategy Fit Assessment Engine for all 5 strategies (BTS, spec, land bank, ground lease, flip)
- Strategy fit ratings stored in parcel_scores.strategy_fit JSONB

**Exit criteria**: Each scored parcel has an actionability status and strategy fit ratings. Parcels with "actionable" status appear in rankings.

---

## Phase 9: Snapshot + Strategy Memo Generation (1 evening, ~2-3 hours)

**Goal**: Human-readable narrative outputs.

Use the three-agent workflow. Implement:
- Per-parcel snapshot generator (markdown file in `snapshots/`) with investment thesis written by Claude
- Per-market strategy memo generator (markdown file in `rankings/`) summarizing the cycle's approach, learnings, pipeline composition, and recommended adjustments

**Exit criteria**: After a discovery cycle, the team can read a strategy memo and individual parcel snapshots without ever touching the database.

---

## Phase 10: First Overnight Autonomous Run (1 evening setup, runs overnight)

**Goal**: The full AutoResearch loop runs autonomously and produces actionable parcels.

Tell Claude Code:

> "Read program.md. You are now operating as the autonomous research agent described in the Overview. Begin the experiment loop. Start with Atlanta market, Fulton County only. Follow the exact loop sequence in program.md — discover, filter, score, run actionability, tag strategy fit, generate snapshots, commit. Log results to research_log. Generate a strategy memo at the end of the cycle. Do not stop until I manually halt you."

**Exit criteria**: Wake up to a strategy memo, ranked parcel list, snapshots, and git history showing the agent's autonomous work overnight.

---

## Phase 11: Expand to Remaining Atlanta Counties (Ongoing, 1 county per evening)

Add Clayton, Henry, Cobb, Gwinnett, DeKalb, Fayette, Spalding to the connector registry. Each new county requires:
1. Validate the county's API endpoint manually (or confirm AI fallback portal)
2. Add to harness registry, run harness validation
3. Tell Claude Code to add the county's connector to research.py (Tier 1 review: one independent fresh-context reviewer per STANDING_RISKS.md)
4. Run discovery cycle against the new county

Priority order per appendix_a_county_connectors.md: Fulton (done) → Clayton → Henry → Cobb → Gwinnett → DeKalb → Fayette → Spalding.

---

## Phase 12: AI Fallback Layer (1-2 evenings, ~3-4 hours)

**Goal**: When ArcGIS APIs fail or don't exist (Spalding, Fayette), the agent can still extract data via Playwright + Claude vision.

Use the full three-agent workflow (Tier 2 — external-service integration). Implement the AI fallback per appendix_a_county_connectors.md. Test against qPublic for a known Fulton parcel first, then enable for Spalding and Fayette.

**Exit criteria**: A simulated API failure for Fulton triggers AI fallback successfully. Spalding and Fayette discovery cycles run via AI fallback.

---

## Phase 13: Tuning Cycle (Ongoing, 1 hour/week)

Once the agent is running across all 8 Atlanta counties:
- Review overnight strategy memos and ranked parcels
- Tune `parameters.json` weights based on which parcels the team actually pursues
- Update `program.md` if strategic direction changes (different corridors, different size ranges, etc.)
- Add new data sources as the agent identifies gaps

This is the steady-state operation. The agent gets better over time as it self-modifies research.py and as you tune parameters based on real deal feedback.

---

## Phase 14+: Multi-Market Expansion (Future)

When Atlanta is producing real deal flow:
- Add Orlando (data infrastructure quality matches Atlanta)
- Add Chicago (more market complexity but data is there)
- Add Lehigh Valley PA as a focused pilot
- Defer NJ until a third-party data subscription decision is made (Daniel's Law issue)

Each new market follows the same pattern: build connectors, run harness, expand corridor bounding boxes, ingest market-specific CoStar exports.

---

## Time Estimate Summary

| Phase | Estimated Time |
|-------|----------------|
| 0: Environment Setup | 2 hours |
| 1: Core Scaffolding | 2 hours |
| 2: Test Harness | 2-3 hours |
| 3: First Connector + Discovery | 3-4 hours |
| 4: Hard Filters Complete | 2 hours |
| 5: Scoring MVP | 3-4 hours |
| 6: CoStar Ingestion | 2 hours |
| 7: Scoring Complete | 2 hours |
| 8: Actionability + Strategy | 3-4 hours |
| 9: Snapshots + Memos | 2-3 hours |
| 10: First Overnight Run | 1 hour setup, runs autonomously |
| **Subtotal to first overnight run** | **24-30 hours** |
| 11: Expand counties | ~1 hour each, 7 counties |
| 12: AI Fallback | 3-4 hours |
| 13: Ongoing tuning | 1 hour/week |

Realistic timeline assuming 2-3 evenings per week of focused work: **6-8 weeks to first overnight run, 10-12 weeks to full Atlanta coverage**.
