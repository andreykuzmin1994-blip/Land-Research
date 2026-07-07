# START_HERE.md — Orientation Chain for Claude Code

> **If you are Claude Code, this is your first read. Do not skip steps. Do not skim.**
> **Do not write code, modify files, or take any action until you reach Step 6.**

---

## Why This File Exists

This repo is a Karpathy AutoResearch implementation for industrial real estate land sourcing. The pattern depends on strict file mutability rules, a metric integrity contract, and a setup phase you complete BEFORE the experiment loop begins. Skipping any of this silently corrupts the entire system.

You are about to walk through a 6-step orientation chain. Each step produces evidence that you completed it. Steps are gated — you confirm completion before proceeding to the next.

---

## Step 1: Acknowledge the Pattern (Read-Only)

**Action**: Read `AUTORESEARCH_MECHANICS.md` in full. This is the canonical specification of the Karpathy pattern as implemented in this project. If anything else in this repo conflicts with that document, that document wins.

**Confirm Step 1 by stating** (in your response back to the human, verbatim, before doing anything else):
> "Step 1 complete. I have read AUTORESEARCH_MECHANICS.md. I understand:
> - The five-file contract: program.md (human-only), prepare.py (immutable during run), parameters.json (immutable during run), sources.json (locked during run), research.py (my sandbox)
> - I edit ONLY research.py during a run
> - The metric (actionable_pipeline_count) is calculated by prepare.py and I cannot modify how it is calculated
> - The experiment loop is keep-or-revert via git ratchet on a dedicated autoresearch/<tag> branch
> - The NEVER STOP rule applies once setup is confirmed
> - The 90-minute experiment budget is hard-enforced"

If you cannot confirm any of these points after reading, re-read the document. Do not proceed until the confirmation is accurate.

---

## Step 2: Read the Strategic Context (Read-Only)

**Action**: Read `program.md` in full. This is the human's strategic direction — what the agent is optimizing, target markets, scoring philosophy, strategy fit assessment, actionability gates.

**Then read**: `appendix_a_county_connectors.md` — focus on the **Coding Workflow: Three-Agent Code Team** section near the top and the **Connector Test Harness** section. These govern how you work, not just what you build.

**Confirm Step 2 by stating**:
> "Step 2 complete. I have read program.md and appendix_a_county_connectors.md. I understand:
> - The agent's primary metric is actionable_pipeline_count
> - The four actionability gates are: path to control (informational only, never a hard fail), plausible entitlement, viable strategy with next step, no deal-killers
> - The five investment strategies the agent tags parcels for: BTS, spec development, land bank, ground lease, land flip, plus assemblage
> - The tiered review workflow (STANDING_RISKS.md): tests+CI at Tier 0, one independent fresh-context reviewer at Tier 1, the full three-agent adversarial workflow (sole commit authority with Agent 3) for Tier 2 metric/contract-touching changes — always on the strongest available model
> - The connector test harness must be built BEFORE individual county connectors"

---

## Step 3: Read the Infrastructure Specs (Read-Only)

**Action**: Read in this order:
1. `STORAGE_ARCHITECTURE.md` — Postgres + PostGIS schema, why not Obsidian, the database tables you will populate
2. `COSTAR_INGESTION_CONTRACT.md` — Manual export workflow, NEVER scrape CoStar
3. `BUILD_PHASES.md` — The 14-phase implementation roadmap

**Confirm Step 3 by stating**:
> "Step 3 complete. I have read the infrastructure specs. I understand:
> - Storage is PostgreSQL + PostGIS, not file-based, not Obsidian
> - CoStar ingestion is via manual scheduled exports only — scraping is a legal risk and is forbidden
> - The implementation roadmap has 14 phases; I will determine the CURRENT phase from the repo itself in Step 4 (README Status + `make status`), not from any prose snapshot in the specs
> - I will not propose alternative storage approaches or alternative CoStar integration methods unless the human explicitly asks"

---

## Step 4: Inventory the Current Repo State

**Action**: Without modifying anything, list what currently exists in this repo. Use `ls`, `find`, or `view` tools to inventory:
- All `.md` documentation files
- All `.json` configuration files
- The current state of `prepare.py`, `research.py`, `connector_harness.py` (do they exist? if so, what's in them?)
- The contents of `markets/`, `rankings/`, `snapshots/`, `harness_reports/`, `flagged/`, `sources/` directories
- The git log (is there an active autoresearch/<tag> branch? if so, the human may want to resume rather than start fresh)

**Confirm Step 4 by reporting**:
> "Step 4 complete. Repo inventory:
> - Documentation files present: [list]
> - Configuration files present: [list]
> - Code files present: [list with brief description of contents, or 'not yet built' for missing files]
> - Runtime directories: [empty / contain N files]
> - Git state: current branch is X, there are/are not active autoresearch branches
> - Based on this inventory, the human is at approximately Phase [N] of BUILD_PHASES.md"

---

## Step 5: Confirm What the Human Wants From This Session

**Action**: Based on the repo state from Step 4 and what the human said in their initial message, articulate your understanding of what they want this session to accomplish. Be specific. Common session types:

- **Specification refinement** — the human wants to discuss or modify the spec documents. No code yet. Skip Step 6.
- **Setup phase** — the human wants you to walk through the AutoResearch setup phase (per AUTORESEARCH_MECHANICS.md → "The Setup Phase"). This includes proposing a run tag, creating a branch, verifying infrastructure, and establishing a baseline.
- **Build a specific phase** — the human wants you to implement a specific phase from BUILD_PHASES.md (e.g., "build the connector harness," "build the Fulton County connector"). This requires the three-agent coding workflow.
- **Run an experiment** — the human wants you to execute one experiment in an existing experiment loop. This requires that setup is already complete and an autoresearch/<tag> branch exists.
- **Continuous loop** — the human wants you to run the autonomous loop. This requires setup complete, then NEVER STOP applies.
- **Debug or diagnose** — the human is asking why something didn't work. Read logs and database state, propose a hypothesis. No code modifications without their approval.

**Confirm Step 5 by stating**:
> "Step 5 complete. My understanding of this session: [specific session type from above]. Specifically, the human wants me to: [concrete description]. Before I proceed, please confirm or correct this understanding."

**STOP HERE and wait for the human's confirmation.** Do not proceed to Step 6 without it.

---

## Step 6: Execute the Confirmed Session Type

Once the human confirms what they want, execute that session type using the appropriate workflow:

### If Specification Refinement
- Edit `.md` files only
- The three-agent workflow is NOT required for documentation changes
- Update README.md cross-references if you add new sections to other docs

### If Setup Phase
- Follow the exact sequence in AUTORESEARCH_MECHANICS.md → "The Setup Phase"
- Propose a run tag (date-and-market based, e.g., `atl-2026-04-30`)
- Create the branch `autoresearch/<tag>` from main
- Verify Postgres connection, harness passes for at least one county, CoStar exports are recent enough
- Run a baseline experiment (research.py UNMODIFIED) and record as the first row of experiment_log.tsv
- Confirm baseline metric value with human BEFORE starting the loop

### If Build a Specific Phase
- Run the three-agent coding workflow (Agent 1 risk review → Agent 2 code → Agent 3 review and commit)
- For phase tasks, read the corresponding section of BUILD_PHASES.md to understand acceptance criteria
- Do not modify prepare.py, parameters.json, or sources.json from within research.py code

### If Run an Experiment
- Read current state of research.py, experiment_log.tsv, and the active branch
- Form a hypothesis based on prior experiment history
- Make ONE focused modification to research.py
- Commit with message `exp: <description>`
- Run `python evaluate.py` (or equivalent) to compute the metric
- Apply keep-or-revert decision per AUTORESEARCH_MECHANICS.md
- Append to experiment_log.tsv

### If Continuous Loop
- Confirm setup is complete (branch exists, baseline recorded, infrastructure healthy)
- Begin the experiment loop
- Apply the NEVER STOP rule — do NOT pause to ask if you should continue
- Halt only on explicit human instruction, 7+ day runs, or catastrophic infrastructure failure

### If Debug or Diagnose
- Read logs, query database state, inspect harness reports
- Propose a hypothesis with evidence
- Do not modify code, configuration, or run new experiments without human approval
- If the diagnosis requires reverting a commit on the experiment branch, stop and confirm with the human first — reverting is a metric-impacting action

---

## Anti-Patterns to Recognize and Avoid

These are mistakes you might be tempted to make. Don't.

### Anti-Pattern 1: Modifying prepare.py to "fix" a metric issue
If actionable_pipeline_count is too low or too high and you want to adjust the actionability gates or composite threshold, that is a SIGNAL that the human needs to mutate prepare.py between runs — not something you do during a run. Halt the loop and tell the human.

### Anti-Pattern 2: Bundling multiple changes per experiment
The Karpathy pattern requires single-variable changes per experiment so the metric movement can be attributed. If you find yourself making 3 changes "while you're in there," stop and split them into 3 sequential experiments.

### Anti-Pattern 3: Skipping the setup phase
Even if you're sure the infrastructure is healthy, run the setup sequence anyway. The baseline experiment matters. Without it, you have no point of comparison for subsequent experiments.

### Anti-Pattern 4: Asking the human "should I continue?"
Once the loop has begun and setup was confirmed, you do not pause for permission. The human might be asleep. Run until manually halted. If you genuinely run out of ideas, re-read program.md and try harder — don't stop.

### Anti-Pattern 5: Scraping CoStar
There is no scenario where scraping CoStar is acceptable. If a CoStar export is missing or stale and you want to "just grab the data directly," DON'T. Use the most recent valid export, log the staleness in the strategy memo, and continue.

### Anti-Pattern 6: Writing code without the three-agent workflow
If you're making a production code change and you only ran one agent, you skipped a required process. Stop, restart with Agent 1 (risk review), and proceed properly.

### Anti-Pattern 7: Adding "sources" to sources.json from within research.py
Adding sources is a vector for metric manipulation. If research.py needs a new source, halt and tell the human to add it to sources.json between experiments.

### Anti-Pattern 8: Treating the experiment_log.tsv as throwaway
The TSV is the canonical experimental history. It accumulates across runs. Append, don't overwrite. Use the exact 7-column schema from AUTORESEARCH_MECHANICS.md.

---

## When You're Genuinely Confused

If something in the spec genuinely doesn't make sense, or if the situation in the repo doesn't match what the spec assumes, STOP and ask the human. Better to halt for 5 minutes of clarification than to corrupt the experiment log with bad commits.

The human would rather you ask "I see the experiment_log.tsv has rows from a prior run on a different branch — should I treat this as a continuation or a fresh run?" than guess wrong.

---

## Final Note

You are operating in an environment where small mistakes (modifying the wrong file, skipping the setup phase, bundling changes) silently corrupt a system that's supposed to produce trustworthy autonomous research. The orientation chain exists because those mistakes are easy to make and catastrophic when made.

Walking through Steps 1–5 takes maybe 10 minutes of reading and confirmation. The cost of skipping it can be days of useless experiment results. Don't skip.

When you're done with Step 5 and the human has confirmed, you proceed with confidence and full context. That's the point.
