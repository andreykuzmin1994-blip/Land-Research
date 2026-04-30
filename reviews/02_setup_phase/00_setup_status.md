# Setup Phase — Status and Pre-Flight Checklist

> Per AUTORESEARCH_MECHANICS.md "The Setup Phase". This document is the
> institutional record of where the project stands relative to the six-step
> setup sequence required before the experiment loop can begin.
>
> Author: orchestrator (this Claude Code session). Date: 2026-04-30.
> Branch at write time: `claude/project-onboarding-sazHe`.

---

## Verdict at the top

**Setup phase HAS NOT begun.** All six steps in AUTORESEARCH_MECHANICS.md
"Setup Sequence" are gated on human-side and code-side prerequisites that have
not yet completed. The autoresearch experiment branch should NOT be created
until the prerequisites listed below are cleared. Creating it earlier would
either (a) require fabricating a baseline metric against an empty database,
which corrupts the experiment log per the "When Mutating prepare.py" protocol,
or (b) leave a stub branch with no baseline, which violates the "first row of
experiment_log.tsv must be `status=baseline`" requirement.

The orchestrator is halting at this point per the START_HERE.md Step 5 instruction
to confirm before acting on metric-impacting actions.

---

## Proposed run tag

`atl-2026-04-30`

Format: `<market>-<YYYY-MM-DD>`. Atlanta is the home market (program.md "Tier 1
Markets"); 2026-04-30 is today and the natural baseline date for the first run.
If the human delays to a new date before unblocking the prerequisites, the tag
should roll forward (`atl-2026-MM-DD`) so the date in the tag matches the date
the baseline experiment actually executes.

The branch will be `autoresearch/atl-2026-04-30` per AUTORESEARCH_MECHANICS.md.

## Proposed branch creation point

Per AUTORESEARCH_MECHANICS.md → "Setup Sequence" step 2: "Create the branch:
`git checkout -b autoresearch/<tag>` from `main`. All experimentation happens
on this branch. Main stays clean."

`main` currently lacks the Phase 1 scaffolding. Phase 1 lives on the development
branch `claude/project-onboarding-sazHe` (per CLAUDE.md repo-level guidance).

**Recommendation**: human merges `claude/project-onboarding-sazHe` → `main`
(via PR review or fast-forward) BEFORE the autoresearch branch is created.
Otherwise the setup phase would either branch off main without prepare.py /
research.py (broken) or branch off the onboarding branch (off-spec). The
orchestrator will not auto-merge to main; that requires human approval.

## Step-by-step status

### Setup Step 1 — Agree on a run tag

- **Status**: PROPOSED. `atl-2026-04-30` per the section above.
- **Blocker**: Human confirmation.
- **Owner to unblock**: Human.

### Setup Step 2 — Create the autoresearch branch

- **Status**: BLOCKED. Cannot create yet.
- **Why**: Branch should be cut from `main`, but `main` does not yet contain
  Phase 1 scaffolding. Cutting from `main` now would produce a branch where
  `prepare.py` is missing.
- **Blocker(s)**: Human merges `claude/project-onboarding-sazHe` → `main`.
  Phase 1 commits on the onboarding branch are: `91ff240` (.gitignore /
  cleanup), `0a1f426` (Agent 1 risk review), `627cbbc` (Phase 1 scaffolding +
  three-agent artifacts).
- **Owner to unblock**: Human.

### Setup Step 3 — Read in-scope files

- **Status**: COMPLETE. The orientation chain at session start covered
  README.md, START_HERE.md, AUTORESEARCH_MECHANICS.md, program.md,
  appendix_a_county_connectors.md, STORAGE_ARCHITECTURE.md,
  COSTAR_INGESTION_CONTRACT.md, BUILD_PHASES.md, parameters.json,
  sources.json. CLAUDE.md was acknowledged.
- **Note**: When the setup phase is later actually executed by the running
  agent, that agent re-performs Step 3 against the autoresearch branch's
  HEAD as its own grounding step. The orientation reading from this session
  does not transfer.

### Setup Step 4 — Verify infrastructure

Four sub-checks. All four are BLOCKED:

#### 4a. Postgres connection (`SELECT POSTGIS_VERSION()` returns)

- **Status**: BLOCKED.
- **Why**: No Supabase project exists. `.env` does not exist (only
  `env.template` exists, populated by Phase 1 but not filled in).
- **Blocker**: Human creates Supabase free-tier project per BUILD_PHASES.md
  Phase 0 step 3, enables PostGIS extension, copies the connection-pooler
  DSN into `.env` against `DATABASE_URL`.
- **Owner to unblock**: Human (Phase 0 task).
- **Verification command, once unblocked**:
  ```
  cd /home/user/Land-Research
  python3 -m venv .venv && source .venv/bin/activate
  pip install -r requirements.txt
  python prepare.py
  ```
  Expected output: PostGIS version line, `actionable_pipeline_count=0`,
  `confidence_weighted_pipeline=0.0`, exit 0.

#### 4b. Connector harness passes for at least one county (Fulton minimum)

- **Status**: BLOCKED.
- **Why**: `connector_harness.py` does not exist. The harness is the Phase 2
  deliverable per BUILD_PHASES.md and per
  `appendix_a_county_connectors.md` → "Connector Test Harness" → "Building
  Order": "Build the harness FIRST (before any individual connector)".
- **Blocker**: Phase 2 implementation. Per appendix, this requires the
  three-agent coding workflow.
- **Owner to unblock**: Future Claude Code session, three-agent workflow
  invoked by the human. The Fulton connector spec (sources.json
  `county_parcel_data.fulton_ga`) is already validated as of 2026-04-30 and
  is ready to be the harness's seed registry entry.

#### 4c. CoStar export folder accessible with files less than 30 days old

- **Status**: BLOCKED.
- **Why**: No `costar_exports/` folder exists. No CoStar saved searches have
  been configured per COSTAR_INGESTION_CONTRACT.md → "Setting Up the Saved
  Searches in CoStar". No email-to-folder pipeline has been wired.
- **Blocker**: Human one-time setup of the five recurring CoStar saved
  searches (submarket stats, land sales comps, building sales comps,
  leasing comps, land listings) plus the email-to-folder routing. Per the
  contract this is a permitted-use CoStar feature; the agent never scrapes.
- **Owner to unblock**: Human (Phase 6 prerequisite).
- **Acceptable reduced state for first run**: per
  AUTORESEARCH_MECHANICS.md → "What Happens If an Export Is Late or
  Missing", the agent can baseline with stale or missing CoStar data and
  the strategy memo flags the staleness. So this gate is technically soft.
  The setup phase still strongly prefers fresh exports for the baseline.

#### 4d. At least one corridor bounding box configured for Atlanta

- **Status**: BLOCKED.
- **Why**: `submarkets.bbox` (PostGIS Polygon, 4326) is the storage location
  per STORAGE_ARCHITECTURE.md. Phase 1 created the table but the table is
  empty. `sources.json` does not carry corridor bboxes (and shouldn't —
  corridors are domain data, not connector configuration).
- **Blocker**: A submarkets seed. program.md lists the Atlanta submarkets
  ("South Fulton, West Atlanta/I-20, I-85 South (Airport/Clayton), I-75
  South (Henry/Spalding), Northeast (Gwinnett/Barrow), I-75 North
  (Bartow/Cherokee)") but does NOT specify exact lat/lon bounding boxes.
  Those need to be either: (i) authored by hand by the human and inserted
  via SQL or a seed script, or (ii) derived programmatically by Phase 3's
  Fulton discovery code as part of building corridor query logic. The
  appendix's "Per-County Connector Specs → Fulton" likely has corridor
  guidance worth re-reading when Phase 3 begins.
- **Owner to unblock**: Phase 3 work (Fulton discovery connector + corridor
  bbox seeding) via the three-agent workflow. May produce a small
  `seed_atlanta_submarkets.py` companion script, OR insert the seed inline
  in `prepare.py`'s schema-apply (the latter being technically a mutation
  to the immutable layer, which is acceptable BETWEEN runs but is the kind
  of thing that should be committed as `prepare-mutation:` per
  AUTORESEARCH_MECHANICS.md).

### Setup Step 5 — Establish baseline

- **Status**: BLOCKED.
- **Why**: Cannot run a baseline experiment because (a) database is empty
  (no parcels), (b) discovery code is a NotImplementedError stub, (c) the
  scoring engine is not built. Even if `python prepare.py` succeeds against
  Supabase, `calculate_actionable_pipeline_count()` returns 0 and the
  baseline row would be `metric=0` — that is a legitimate baseline value,
  but it is not informative because the agent cannot improve it without
  Phase 3+ code.
- **Karpathy correctness note**: per AUTORESEARCH_MECHANICS.md a metric=0
  baseline IS the technically correct first row of `experiment_log.tsv`
  for the empty-research.py state. But the human almost certainly does not
  want the experiment loop to begin yet, because no improvement is possible
  until Phase 3 ships discovery. Establishing a baseline of 0 now and then
  building Phase 3 on the same branch would force a `prepare-mutation:`
  reset later. Better: defer the baseline until Phases 2, 3, 4, 5, 6, 7,
  and 8 ship at minimum. Per BUILD_PHASES.md, that's the Phase 10 boundary
  ("First Overnight Autonomous Run").
- **Owner to unblock**: Phases 2–9 complete, then human triggers setup.

### Setup Step 6 — Confirm with human and begin loop

- **Status**: BLOCKED.
- **Owner to unblock**: All upstream gates clear.

---

## Implementation Checklist mapped to Phase 1 commit

Per AUTORESEARCH_MECHANICS.md → "Implementation Checklist" (17 items). State as
of commit `627cbbc`:

| # | Item | Phase 1 status |
|---|------|----------------|
| 1 | `prepare.py` contains metric calc, gates, hard filter logic, composite formula, evaluation universe | PARTIAL — metric calc + universe present; gates/filters/formula stubbed for Phase 4/5/8 |
| 2 | `prepare.py` has immutability header | PASS |
| 3 | Agent's CLAUDE.md tells it not to modify prepare.py / parameters.json | PASS (existing CLAUDE.md + START_HERE.md cover this) |
| 4 | `parameters.json` has `_immutable_during_run: true` | PASS |
| 5 | `research.py` imports from `prepare.py` and never redefines | PASS |
| 6 | Setup phase implemented as discrete sequence | NOT YET (this document is the manual version; the setup-phase code that the autonomous agent runs is Phase 10 work) |
| 7 | Baseline experiment required as first row of experiment_log.tsv | NOT YET (no experiment_log.tsv yet; no baseline) |
| 8 | Hard 90-min timeout enforced at OS level | PASS — `prepare.run_with_os_timeout` |
| 9 | `git reset --hard HEAD~1` is the revert mechanism | NOT YET (no loop runner yet) |
| 10 | Every commit on experiment branch corresponds to a `keep` row | NOT YET |
| 11 | TSV is in `.gitignore` | PASS — `.gitignore` carries `experiment_log.tsv` |
| 12 | Branch naming convention `autoresearch/<tag>`, main never written during run | NOT YET (no run yet) |
| 13 | Agent cannot modify prepare.py / parameters.json / sources.json from research.py | PASS at the language level: research.py imports from prepare.py; parameters are MappingProxyType; verify_parameters_unchanged sentinel detects on-disk drift. Filesystem permissions are NOT enforced; this is a convention guarded by the orientation prompt and three-agent review. |
| 14 | Simplicity criterion in program.md and prompt | PASS (program.md and AUTORESEARCH_MECHANICS.md both reference) |
| 15 | NEVER STOP rule in program.md and prompt | PASS |
| 16 | When prepare.py mutated between runs, protocol followed (new branch + baseline) | PROCEDURAL — to be enforced by future setup runs |
| 17 | Cross-run TSV accumulation implemented | NOT YET |

Five PASS, three PROCEDURAL/PARTIAL, eight NOT YET. The eight not-yet items are
all loop-runtime concerns that come online at Phase 10.

---

## Dependency chain to first overnight run

The shortest path from "now" to "setup phase can begin" passes through:

1. **Human** merges `claude/project-onboarding-sazHe` → `main`.
2. **Human** completes Phase 0: provisions Supabase, enables PostGIS, populates `.env`.
3. **Human** runs `python prepare.py` against Supabase to verify schema apply succeeds (Phase 1 exit criterion). Reports back; if not green, three-agent workflow does a Phase 1 fix-forward.
4. **Three-agent workflow** builds Phase 2: `connector_harness.py` per appendix_a_county_connectors.md → "Connector Test Harness".
5. **Three-agent workflow** builds Phase 3: Fulton County discovery connector. Includes seeding the Atlanta submarkets bbox row(s) for at least South Fulton (the corridor bbox infrastructure verification gate).
6. **Three-agent workflow** builds Phase 4: H5–H10 hard filters.
7. **Three-agent workflow** builds Phase 5: scoring MVP (S1, S2, S3, S7, S8, S9 stub, S10, S11, S12).
8. **Human** completes the CoStar saved-search setup per COSTAR_INGESTION_CONTRACT.md (Phase 6 human prerequisite).
9. **Three-agent workflow** builds Phase 6: ingestion pipeline.
10. **Three-agent workflow** builds Phase 7: scoring complete (S4, S5, S6, refined S8).
11. **Three-agent workflow** builds Phase 8: actionability + strategy fit.
12. **Three-agent workflow** builds Phase 9: snapshot + memo generation.
13. **Setup phase** finally runs against Phase 9 code on `autoresearch/atl-2026-04-30` (or whatever date applies). Baseline metric established. NEVER STOP rule activates.

Step 13 is BUILD_PHASES.md Phase 10 — "First Overnight Autonomous Run". Per that
document the realistic timeline is 6–8 weeks of human evening/weekend work.

---

## Halt point

The orchestrator halts here. Concrete next actions for the human:

- **Confirm** the proposed run tag `atl-2026-04-30` (or correct it).
- **Confirm** the branching strategy (merge onboarding branch to main first,
  then create autoresearch branches off main).
- **Decide** whether to continue this session by having the orchestrator
  stand up Phase 2 (the connector harness) via the three-agent workflow, OR
  pause to provision Supabase first so Phase 1 can be validated end-to-end
  against a real database before more code is layered on.

This document will sit on the onboarding branch alongside the three-agent
artifacts as a permanent record of where the project stood when orientation
ended and Phase 1 shipped.

---

## Resolutions — 2026-04-30 follow-up

The three halt-point asks were answered by the human in the same session:

### Resolution 1 — Run tag convention

**Adopted: option C** (phase-build dev branches now; `autoresearch/<tag>` cut
only when the setup phase actually runs, with the tag's date matching the
real baseline date — not today's date).

Operational consequences:

- The proposed `autoresearch/atl-2026-04-30` tag is **withdrawn**. No
  autoresearch branch will be created until Phases 2–9 ship and the human
  triggers the setup phase.
- All Phase 2–9 development happens on dev branches (currently
  `claude/project-onboarding-sazHe`; future phases may use new dev branches
  cut from `main`). These dev branches are NOT autoresearch branches and
  do NOT carry the `autoresearch/<tag>` naming convention.
- When the setup phase eventually runs, the agent picks the tag fresh from
  that day's date (e.g., `atl-2026-06-15` if the baseline runs on 2026-06-15).
- Cross-run TSV history: per AUTORESEARCH_MECHANICS.md "Cross-Run Aggregation"
  the `experiment_log.tsv` accumulates across runs/tags. Each run's first row
  is `status=baseline`, so the file remains parseable even with multiple tags.

### Resolution 2 — Branching strategy

**Adopted: (i)** — the human will review and merge
`claude/project-onboarding-sazHe` → `main` when they choose. The orchestrator
does NOT auto-merge.

Implied corollaries (also in effect):

- Future Phase 2+ dev work continues on dev branches that merge back to
  `main` after human review. Whether each phase gets its own dev branch or
  several phases share one is the human's preference; the spec doesn't care.
- Only the setup-phase agent ever creates an `autoresearch/<tag>` branch,
  and only off a clean `main`. This preserves the AUTORESEARCH_MECHANICS.md
  invariant that "main stays clean" during a run.

The orchestrator has not opened a pull request. The branch is pushed at
`origin/claude/project-onboarding-sazHe` and the human can merge via the
GitHub UI / locally / or by asking the orchestrator to open a PR via MCP.

### Resolution 3 — Next action

**Adopted: Supabase provisioning before Phase 2**.

The human will:

1. Create a Supabase free-tier project (project name suggestion:
   `land-site-selector` or similar).
2. Enable the PostGIS extension via the Supabase SQL editor:
   ```sql
   CREATE EXTENSION IF NOT EXISTS postgis;
   SELECT POSTGIS_VERSION();
   ```
3. Copy the **connection pooler** endpoint (NOT the direct DB endpoint)
   from Supabase project settings → Database → Connection string. The
   pooler is required per STORAGE_ARCHITECTURE.md → "Connection Pattern":
   "the connection pooler endpoint should be used for the agent's
   autonomous loop (avoids connection limit issues during long-running
   cycles)".
4. Create `.env` in the repo root from `env.template` and paste the DSN
   into `DATABASE_URL`. Leave `ANTHROPIC_API_KEY` blank for Phase 1.
5. Validate Phase 1 end-to-end:
   ```bash
   cd /home/user/Land-Research
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   python prepare.py
   ```
   Expected: PostGIS version line, `actionable_pipeline_count=0`,
   `confidence_weighted_pipeline=0.0`, exit 0.
6. Report green/red back to the next orchestrator session. Capture stdout
   and any traceback verbatim.

**If green**: Phase 1 exit criterion (BUILD_PHASES.md) is satisfied. Next
session begins Phase 2 (`connector_harness.py` via three-agent workflow).

**If red**: the next session runs a Phase-1 fix-forward via the three-agent
workflow. Likely failure modes per Agent 1's risk review:

- PostGIS extension permission denied on Supabase free tier → may require
  Supabase Pro, OR the human may need to enable PostGIS via the Supabase
  Dashboard UI rather than via SQL.
- `psycopg[binary]>=3.1` install failure on the user's Python — fall back
  to `psycopg2-binary` (Agent 2 documented the migration path).
- Connection-string parsing edge cases (`postgresql+psycopg://` vs
  `postgresql://`) — adjust the `_get_connection_dsn` helper.

### Status of this document

All three halt-point asks resolved. The orchestrator hands off to the human
for Supabase provisioning. The next orchestrator session — invoked by the
human after they report Phase 1 green — picks up at "Phase 2: Connector Test
Harness" per BUILD_PHASES.md, reading the three-agent artifacts in
`reviews/01_phase1_scaffolding/` and this status document for institutional
context.
