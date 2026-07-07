# AutoResearch Mechanics

> The full specification of how the Karpathy AutoResearch pattern is implemented for the Land Site Selector.
> This document is canonical. If anything in `program.md`, `appendix_a_county_connectors.md`, or implementation code conflicts with this document, this document wins.
> Adapted directly from [Karpathy AutoResearch](https://github.com/karpathy/autoresearch) (March 2026).

---

## Why This Document Exists

The Karpathy AutoResearch pattern works because of a specific set of mechanics, not just because there's "an agent in a loop." Without these mechanics, the loop silently corrupts itself: the agent finds clever ways to game the metric by modifying the evaluation criteria rather than improving its actual research output, and you wake up to a "high score" that means nothing.

This document specifies the mechanics in operational detail. Every implementation decision about file mutability, evaluation independence, the experiment loop, the git ratchet, and the experiment log derives from preserving the integrity of the metric.

---

## The Five-File Contract

Karpathy's pattern uses three files. We use five because our domain has a database and external data sources that Karpathy's didn't. The mutability rules are stricter than they look — read them carefully.

### File 1: `program.md` — Strategic Direction

**Owner**: Human only.
**Agent permissions**: READ only.
**Purpose**: Defines what the agent is trying to optimize, the rules of the game, and the strategic priorities.

The human edits this between runs to redirect the agent's focus, refine criteria, or clarify priorities. The agent reads it at the start of every run and may re-read it during the loop, but never modifies it.

**This file does NOT define the metric calculation itself** — that lives in `prepare.py`. This file defines the strategic context (target markets, scoring philosophy, strategy fit assessment, actionability gates) but the actual measurement code is in the immutable layer.

### File 2: `prepare.py` — Immutable Measurement Infrastructure

**Owner**: NEITHER human nor agent during a run.
**Agent permissions**: READ only. NEVER EDIT.
**Human permissions during a run**: READ only. NEVER EDIT.
**Human permissions between runs**: May edit, but doing so invalidates the entire run history (see "When Mutating prepare.py" below).

This file is the equivalent of Karpathy's `prepare.py`. It defines:

1. **The metric calculation**: a function `calculate_actionable_pipeline_count(parcels: List[Parcel]) -> Metric` that takes a list of scored parcels and returns the metric. This function is the ground truth.
2. **The actionability gates**: the four-gate evaluation that determines whether a parcel passes (entitlement plausible, viable strategy, no deal-killers, plus the informational path-to-control gate). These rules are LOCKED for the duration of the run.
3. **The hard filter logic**: H1–H10 from `program.md` are implemented here as immutable functions. The agent CANNOT change what counts as a flood-zone failure, an environmental failure, or an out-of-range acreage.
4. **The composite score formula**: the weighted average calculation that combines sub-scores into a composite. The FORMULA is locked here. The WEIGHTS live in `parameters.json` (also immutable during a run, see below).
5. **The evaluation universe definition**: how to query the parcel database to assemble the set of parcels that count toward the metric. This includes the SQL or query logic that defines "all scored parcels in the active experiment branch."
6. **Time budget enforcement**: kills the experiment at 90 minutes wall clock.

**Why this file is immutable**: if the agent could modify `calculate_actionable_pipeline_count()`, it would inevitably "improve" the metric by relaxing the actionability gates rather than by improving discovery and scoring. The metric becomes meaningless. The same is true for hard filters, the score formula, and the evaluation universe — any of these is a vector for self-corruption.

**Why this file is also locked from the human during a run**: if the human edits `prepare.py` mid-run, the metric values across the experiment log are no longer comparable. The git history would show "improvements" that actually came from a metric definition change. Locking the human out preserves the run's integrity.

### File 3: `parameters.json` — Locked Tuning Knobs

**Owner**: Human only.
**Agent permissions**: READ only.
**Human permissions during a run**: READ only. NEVER EDIT during a live run.
**Human permissions between runs**: May edit. Editing starts a fresh run (new branch, new baseline).

This file holds the scoring weights, composite threshold, acreage range, and other tunable values that affect the metric calculation. It is NOT in `prepare.py` because the human wants to tune these between runs without editing Python code, but it IS effectively immutable during a run for the same reason `prepare.py` is.

The agent's loop reads `parameters.json` ONCE at the start of the run and caches the values. Reloading mid-run is forbidden because it would let the agent change the metric mid-stream.

### File 4: `research.py` — The Agent Sandbox

**Owner**: Agent during a run.
**Agent permissions**: FULL EDIT.
**Human permissions during a run**: Don't touch unless the agent is stuck.

This is the equivalent of Karpathy's `train.py`. The agent modifies this file freely. It contains:

1. **Discovery logic**: how to query county APIs, which corridors to prioritize, which heuristics to apply for off-market identification (including the per-county ArcGIS wrappers and, in Phase 12+, AI-fallback navigation).
2. **Hard-filter predicates**: the H1–H10 implementations that decide pass/reject/flag per parcel (the RULES of what counts as a failure are specced in `program.md`; the composite/metric consequences are locked in `prepare.py`).
3. **Scoring implementation**: the sub-score calculation functions for S1–S12 (note: the WEIGHTING is in `parameters.json` and the composite FORMULA in `prepare.py`, but the per-parameter SCORING is here — the agent can improve how it determines "interstate proximity score" for a parcel, but cannot change how that score is combined into the composite).
4. **Strategy fit assessment**: the logic that tags each parcel with strategy fit ratings.
5. **Actionability gate evaluation**: the four-gate logic that produces the PASS/FAIL inputs the metric filters on.

**What does NOT live here** (split out 2026-07-07; all immutable during a run, same status as `prepare.py`):

- `runner.py` — the experiment loop, setup verification, evaluator, TSV I/O, keep-or-revert recording, and the non-kept-experiment purge. The harness that judges the agent's edits must not be editable by the agent mid-run.
- `costar_ingest.py` — the CoStar export ETL, frozen to `COSTAR_INGESTION_CONTRACT.md`.
- `reporting.py` — snapshot and strategy memo rendering.
- `pipeline_common.py` — shared paths, the `flagged_items` helper, shared SQL.

**The critical separation**: `research.py` produces parcel records and sub-scores. `prepare.py` evaluates those records to produce the metric, and `runner.py` runs the loop that records the outcome. The agent can change how it produces the inputs, but it cannot change how they are evaluated or how the ratchet is recorded.

### File 5: `sources.json` — Locked Data Source Registry

**Owner**: Human only.
**Agent permissions**: READ only.
**Human permissions during a run**: May add new sources between experiments (NOT during a single experiment).
**Human permissions between runs**: Full edit.

The agent cannot add sources to this file because adding a source could be a vector for metric manipulation (e.g., adding a "source" that's actually a list of pre-qualified parcels would inflate the pipeline count). New sources require human approval and addition to the registry between experiments.

---

## The Setup Phase

Before any experiment loop begins, the agent runs a setup sequence with the human. This is non-negotiable. Karpathy's program.md does this and we replicate it.

### Setup Sequence

1. **Agree on a run tag**: The agent proposes a tag based on today's date and market (e.g., `atl-2026-04-30` for an Atlanta run starting April 30). The branch `autoresearch/<tag>` must not already exist. If it does, propose a suffix (e.g., `-2`).

2. **Create the branch**: `git checkout -b autoresearch/<tag>` from `main`. All experimentation happens on this branch. Main stays clean.

3. **Read the in-scope files**: The agent reads `README.md`, `program.md`, `appendix_a_county_connectors.md`, `STORAGE_ARCHITECTURE.md`, `prepare.py`, `parameters.json`, `sources.json`, and the current `research.py`. The 2026-07-07 split exists precisely to keep this reading list tractable: the agent's editable surface (`research.py`) is ~3k lines; `runner.py`/`costar_ingest.py`/`reporting.py` are immutable infrastructure the agent consults only as needed.

4. **Verify infrastructure**:
   - Postgres connection works (`SELECT POSTGIS_VERSION()` returns)
   - Connector harness passes for at least one county (Fulton minimum for Atlanta runs)
   - CoStar export folder is accessible and contains files less than 30 days old
   - At least one corridor bounding box is configured for the target market

5. **Establish baseline**: Run ONE complete experiment with `research.py` UNMODIFIED to record the baseline metric value. This is committed as the first row of `experiment_log.tsv` with `status=baseline`.

6. **Confirm with the human and begin**: The agent shows the baseline metric, the run tag, the branch name, and the contents of `program.md` it parsed. The human confirms. Once confirmed, the experiment loop begins and the agent does NOT pause to ask if it should continue.

### Why a Setup Phase Matters

Without a setup phase, the agent can't establish a meaningful "improvement" baseline because it has no idea what the current metric is. The first experiment's metric becomes the implicit baseline, but if that first experiment was an experiment and not a baseline run, you've conflated two different things. Karpathy explicitly requires a baseline run as the first experiment.

---

## The Experiment Loop

This is the heart of AutoResearch. The agent runs this loop forever until the human halts it.

```
LOOP FOREVER:
    1. Read git state — current branch, current commit, last metric
    2. Form a hypothesis — based on results.tsv history, what to try next
    3. Modify research.py — make ONE focused change (NOT bundles of changes)
    4. git commit -m "exp: {description}"
    5. Run experiment — invoke evaluate.py which uses prepare.py to calculate metric
    6. Read result — extract metric from log file via grep
    7. Decide:
       - If metric IMPROVED: keep commit (branch advances)
       - If metric EQUAL or WORSE: git reset --hard HEAD~1 (revert)
       - If experiment CRASHED: log crash, attempt fix only if trivial, otherwise skip
    8. Append to experiment_log.tsv
    9. If 90 minutes elapsed without producing a metric: kill, log timeout, revert
    10. GOTO 1
```

### What Counts as "An Experiment"

Karpathy's experiments are 5-minute training runs on a single GPU. Ours are different in nature but share the same logical structure. **One experiment = one modification to `research.py` that produces a measurable change in the metric.**

In our domain, an experiment looks like:
- The agent modifies one aspect of `research.py` (e.g., adds a new discovery heuristic for tax-delinquent parcels, or changes the spatial buffer around industrial corridors, or adds a new source connector)
- The agent runs the discovery + scoring cycle through `evaluate.py` against the SAME parcel universe definition
- `prepare.py` calculates the metric (`actionable_pipeline_count`) from the resulting parcel records
- The agent compares the metric to the prior baseline

### The Time Budget

Karpathy uses 5 minutes for ML training. Ours is necessarily longer because we're hitting external APIs that have rate limits.

**Fixed time budget per experiment: 90 minutes wall clock.**

This breaks down as:
- ~60 minutes max for discovery cycles across one or more counties
- ~20 minutes max for scoring and actionability evaluation
- ~10 minutes max for snapshot and metric calculation

If the experiment exceeds 90 minutes without producing a metric, `prepare.py` kills it (sends SIGTERM, then SIGKILL after 30 seconds), the experiment is logged as `timeout`, and the agent reverts.

90 minutes per experiment means roughly 16 experiments per 24-hour run. Over a one-week period, that's ~110 experiments. This is comparable in volume to Karpathy's overnight runs (~100 experiments) just spread over a longer wall clock period.

### Why Not Faster?

We tried a shorter budget. Two reasons it doesn't work:
1. County GIS APIs rate-limit at 1 request per second per source. Pulling enough parcels to detect a meaningful pipeline change takes time.
2. CoStar exports are weekly/monthly cadence. The market data layer doesn't refresh fast enough to make sub-hour experiments meaningful.

If we had unlimited API budget and real-time market data, the experiment could be faster. We don't, so 90 minutes is the floor.

---

## The Metric

### Primary Metric: `actionable_pipeline_count`

```
actionable_pipeline_count = COUNT(parcels WHERE
    hard_filters = ALL_PASS
    AND composite_score >= composite_threshold
    AND actionability = PASS
    AND scored_in_current_run = TRUE      -- run_tag = active autoresearch/<tag>
)
```

**Higher is better.** This is the equivalent of Karpathy's `val_bpb` (where lower is better — direction is just convention).

**Run scoping and the data ratchet** (prepare-mutation 2026-07-07): every
`parcel_scores` row carries the `run_tag` of the run and the
`experiment_id` of the `evaluate()` invocation that wrote it. The metric
counts each parcel's latest row *within the active run only* — a fresh run
starts from a fresh universe and re-scores parcels for itself, so baselines
are comparable across runs. When an experiment's decision is `discard`,
`crash`, or `timeout`, the runner deletes that experiment's rows: `git
reset --hard HEAD~1` reverts the code, the purge reverts the data. Without
the purge, a rejected experiment's scores would persist and inflate every
subsequent measurement, silently breaking single-change attribution.

One caveat is inherent to a stateful pipeline metric: within a run, an
experiment can also move the metric by scoring backlog parcels discovered
earlier (data progression), not only by being a better `research.py`. The
tertiary `discovery_rate` / `conversion_rate` tracking exists for the human
to spot this pattern; treat single-experiment deltas as evidence, not proof.

**Purge scope and known residue paths** (Tier-2 review, 2026-07-07):

- The purge covers **`parcel_scores` only**. A discarded experiment's
  `research_log` rows, `flagged_items` rows, and `parcels` UPSERTs are NOT
  reverted — logs are append-only history by design, and discovery is
  idempotent (any later cycle would re-discover the same parcels). Expect
  those side tables to grow across discarded experiments.
- A process killed mid-experiment (SIGKILL/OOM/Ctrl-C) can leave rows whose
  `experiment_id` no purge will target. Every experiment's id is stamped
  into its TSV description (`exp=<id>`) so orphans are discoverable:
  reconcile by deleting run rows whose id is absent from the TSV's
  baseline/keep rows before re-baselining.
- Failed purges are retried at each iteration boundary; five consecutive
  crash/timeout iterations trip a breaker and halt the loop rather than
  purging and repeating the identical workload forever.
- Snapshots and memos (`reporting.py`) read UNSCOPED latest scores — they
  describe the firm's full accumulated pipeline, not the run-scoped ratchet
  number, so a memo's counts and the TSV metric can legitimately differ.
- Two clones on the same branch against the same database share a `run_tag`
  and will see each other's rows and purges. The loop lock is per-clone
  (flock); one loop per run tag is an operator responsibility.

### Why This Metric Is Karpathy-Compliant

Karpathy chose `val_bpb` because it's vocabulary-size-independent. The agent can change tokenization, vocabulary, or model architecture and still get a fair comparison. The metric is meaningful in absolute terms (compression rate) and stable across the kinds of changes the agent will make.

`actionable_pipeline_count` has the same property: the agent can change discovery methods, scoring sub-functions, source connectors, or strategy fit logic, and the metric remains meaningful. Why?

Because the metric is computed by `prepare.py` against a fixed parcel universe definition (the active experiment branch's parcels) using a fixed evaluation function (the four-gate actionability + composite threshold + hard filters). The agent CAN'T change those — they're in the immutable layer.

What the agent CAN do is:
- Discover new parcels that wouldn't otherwise have been in the universe
- Score parcels more accurately so they correctly clear the threshold
- Improve sub-score calculations so the composite is more accurate
- Add data sources that improve coverage of scoring parameters

All of these legitimately improve the metric. None of them game it.

### Secondary Metric: `confidence_weighted_pipeline`

```
confidence_weighted_pipeline = SUM(
    confidence_score for each parcel in actionable_pipeline_count
)
```

This prevents the agent from gaming the primary metric by pushing more parcels into the pipeline at low confidence. A parcel scored on 4 of 12 sub-scores with mostly nulls gets a lower confidence score than one scored on 12 of 12. If the primary metric is tied between two experiments, the one with higher confidence-weighted pipeline wins.

### Tertiary Tracking: `discovery_rate` and `conversion_rate`

These are NOT used for the keep/revert decision. They're logged for human review.
- `discovery_rate`: new actionable parcels per experiment
- `conversion_rate`: percentage of qualified parcels (score ≥ threshold) that also pass actionability

Tracking these helps the human spot when the agent is in a local optimum (high primary metric, declining discovery rate = exhausting known parcels rather than finding new ones).

---

## The Git Ratchet

This is the mechanic that makes the loop monotonic.

### Branch Strategy

```
main
└── autoresearch/atl-2026-04-30          ← active experiment branch
    ├── commit: baseline (research.py unmodified)
    ├── commit: exp: add tax delinquency discovery   ← improved, kept
    ├── commit: exp: tighten spatial buffer 0.04°    ← improved, kept
    │   ↑ failed experiment was here, reverted via git reset --hard
    └── commit: exp: add development authority sources  ← improved, kept
```

Each successful experiment is one commit on the experiment branch. Failed experiments are reverted via `git reset --hard HEAD~1` and leave NO trace in the branch history (only in `experiment_log.tsv`).

### The Keep-or-Revert Decision

After each experiment:

```python
if experiment_status == "crash":
    git_reset()
    log_to_tsv(status="crash", description=description)
    
elif new_metric > baseline_metric:
    # KEEP: branch advances
    log_to_tsv(status="keep", description=description, metric=new_metric)
    baseline_metric = new_metric
    
elif new_metric == baseline_metric:
    # TIE: use confidence-weighted as tiebreaker
    if new_confidence > baseline_confidence:
        log_to_tsv(status="keep", description=description, metric=new_metric)
        baseline_confidence = new_confidence
    else:
        git_reset()
        log_to_tsv(status="discard", description=description, metric=new_metric)
        
else:  # new_metric < baseline_metric
    git_reset()
    log_to_tsv(status="discard", description=description, metric=new_metric)
```

### The Simplicity Criterion

Karpathy explicitly weighs simplicity. We adopt this verbatim. From his program.md: *"All else being equal, simpler is better. A small improvement that adds ugly complexity is not worth it. Conversely, removing something and getting equal or better results is a great outcome — that's a simplification win."*

For our domain, simplicity translates to:
- A 1-parcel improvement that adds a new external API dependency? Probably not worth it.
- A 1-parcel improvement that comes from removing dead code in a connector? Definitely keep.
- An improvement of ~0 actionable parcels but cleaner research.py? Keep.

The agent must explicitly reason about complexity cost when making keep/revert decisions on marginal experiments.

### Soft Constraints

Karpathy treats VRAM as a soft constraint. We have analogous soft constraints:

- **External API call budget**: experiments shouldn't dramatically increase the number of API calls per cycle. An experiment that 10x's the call volume to county servers is unacceptable even if it improves the metric, because it'll get rate-limited or banned.
- **Wall clock budget**: experiments should complete well under the 90-minute hard ceiling. Consistent ~85-minute runs are flagged.
- **Database growth**: experiments shouldn't produce dramatically more parcel records. 10x growth in parcel volume is suspicious — the agent is probably loosening discovery filters, not improving them.

The agent considers these when evaluating marginal improvements.

---

## The Experiment Log: `experiment_log.tsv`

This is the equivalent of Karpathy's `results.tsv`. Tab-separated, NEVER committed to git, lives at the repo root.

### Schema

```
commit	metric	confidence	api_calls	wall_clock_min	status	description
```

1. **commit**: 7-char short git hash
2. **metric**: actionable_pipeline_count (integer)
3. **confidence**: confidence_weighted_pipeline (float, .2f)
4. **api_calls**: total external API calls during the experiment (integer)
5. **wall_clock_min**: total wall clock minutes (.1f)
6. **status**: `baseline`, `keep`, `discard`, `crash`, `timeout`
7. **description**: brief text — what the experiment tried (no tabs, no commas in description; commas are tolerated unlike Karpathy's because we're TSV but periods/dashes are preferred)

### Example

```
commit	metric	confidence	api_calls	wall_clock_min	status	description
a1b2c3d	14	11.2	847	62.4	baseline	initial baseline with current research.py
b2c3d4e	19	15.8	912	68.1	keep	added tax delinquency discovery for fulton
c3d4e5f	19	15.8	904	65.7	discard	tightened spatial buffer to 0.03 degrees
d4e5f6g	0	0.0	0	0.0	crash	added regrid api integration - missing api key
e5f6g7h	22	18.1	1140	71.2	keep	added cobb county connector
f6g7h8i	23	19.4	1156	74.0	keep	added development authority site inventory source
```

### Why Untracked

Karpathy keeps `results.tsv` out of git so the experiment log doesn't pollute the commit history. We do the same. The TSV is a parallel record that accumulates over time and survives across runs.

### Cross-Run Aggregation

When the next run starts (new tag, new branch), the new run's `experiment_log.tsv` rows are appended to the existing file. The file accumulates the firm's full experimental history across all runs. This is valuable for trend analysis and for seeding future agents with prior learning.

---

## The NEVER STOP Rule

Once setup is confirmed and the loop begins, the agent does NOT pause to ask the human if it should continue. It does NOT ask "should I keep going?" or "is this a good stopping point?" Karpathy is emphatic about this and we adopt it verbatim.

The human might be asleep, in a meeting, attending to family, anywhere. The agent runs until manually halted.

### What "Manually Halted" Means

The agent stops only on:
1. The human sends an explicit halt instruction in chat
2. The 90-minute experiment timeout fires AND the agent has been running for more than 7 days (so it gracefully concludes long runs)
3. A catastrophic infrastructure failure (database unreachable for >1 hour, all county connectors failing, etc.)

The agent does NOT stop because:
- It "thinks it's done" — there's always more to try
- The metric plateaus — try harder, more radical changes
- It encountered a tricky bug — fix or skip and continue
- It hasn't seen the human in a while — so what

### When Stuck

If the agent runs out of obvious ideas, it should:
1. Re-read `program.md` and the appendix for ideas it underweighted
2. Look at the `experiment_log.tsv` for near-miss experiments and try combinations
3. Try more radical changes (new corridor strategies, new data sources, completely different scoring approaches)
4. Try the inverse of recent successful experiments to see if they're at a local optimum
5. Read the Karpathy AutoResearch README for inspiration on the loop pattern itself

The agent does not stop just because it ran out of incremental ideas.

---

## Crash Handling

Following Karpathy's pattern verbatim.

### Trivial Crashes — Fix and Retry

- Typo in code → fix it, run again
- Missing import → add it, run again
- Off-by-one error → fix, run again

These count as part of the same experiment. They don't get logged as separate rows in the TSV.

### Non-Trivial Crashes — Skip and Move On

- The experimental idea itself is fundamentally broken (e.g., trying to query a service that doesn't exist with no plausible alternative)
- A dependency is missing (the agent cannot install dependencies — it can only use what's in `pyproject.toml`)
- A database constraint violation that suggests the agent's understanding of the schema is wrong

Log as `crash` in TSV with a brief description, revert to baseline, move on to a different idea. Do not spend more than ~3 fix attempts before giving up.

---

## When Mutating `prepare.py` (Between Runs Only)

Sometimes the human needs to change the metric calculation, the actionability gates, or the hard filters. This is allowed BUT it terminates the current run and invalidates the experiment log going forward.

### The Mutation Protocol

1. The human halts the active run (manual halt instruction).
2. The human edits `prepare.py`, `parameters.json`, or other immutable files.
3. The human commits the change to `main` with a clear message: `prepare-mutation: <what changed>`.
4. The next run starts a NEW branch with a NEW tag. It does NOT continue the prior branch.
5. The first experiment of the new run is a baseline establishing the new metric value under the new rules.
6. `experiment_log.tsv` continues to accumulate, but the human (and any future analysis) must understand that metric values before the mutation commit are NOT comparable to values after.

### Why Such a Strict Protocol

If the human silently edits `prepare.py` mid-run, the experiment log shows "improvements" that came from the rule change rather than the agent's work. The git ratchet's monotonicity guarantee is broken. Future analysis of "what worked" becomes unreliable.

The protocol forces the human to make the rule change explicit, branch off cleanly, and re-establish a baseline. This preserves the integrity of the AutoResearch pattern.

---

## Known Limitations (Inherited from the Pattern)

Karpathy is honest about the limitations of his pattern. We inherit them.

### The Local Optimum Problem

The ratchet only accepts changes that immediately improve the metric. The agent cannot take a step backward to set up a larger gain. Human researchers reason "it'll get worse before it gets better" and can pursue a strategy that initially looks bad. The agent can't.

In our domain this means: the agent will tend to find incremental improvements (slightly better corridor selection, marginal scoring tweaks) rather than breakthrough strategies (e.g., a completely new sourcing methodology that requires substantial setup before it pays off).

**Mitigation**: the human should periodically inject experiments via `program.md` updates that mandate trying specific bold ideas. The agent will execute them within the ratchet (and revert if they don't immediately improve), but the human can identify them as "near-miss bold ideas" in the TSV and re-attempt them with refinements in subsequent runs.

### The "Cagy Agent" Problem

Karpathy notes the agent "feels cagy and scared" on open-ended problems, attributing this to RLHF training that rewards safe, conservative outputs. The agent will reach for incremental improvements rather than radical reimaginings.

**Mitigation**: explicit prompting in `program.md` to "try something radical at least once per 10 experiments" helps. So does the human reviewing the TSV and noting when the agent has been incremental for too many cycles in a row.

### The Single-Variable Problem

Karpathy's pattern works best when the agent makes ONE change per experiment. Bundling multiple changes makes it impossible to attribute the metric movement to any one of them.

In our domain this is harder because some changes are naturally bundled (adding a new county connector requires changes to discovery, scoring, and source registry simultaneously). The agent should isolate changes as much as possible and explicitly note in the experiment description when a change is necessarily bundled.

### The Platform-Specificity Problem

Karpathy notes results are platform-specific (an H100 finding doesn't transfer to an RTX 4090). For us, results are firm-specific. An optimal scoring weight for our buy box doesn't transfer to another firm's. This is fine — it's why `parameters.json` is human-tuned per firm.

---

## Comparison Table: Karpathy vs. Land Site Selector

| Karpathy | Land Site Selector |
|----------|---------------------|
| `program.md` (human-edited) | `program.md` (human-edited) |
| `prepare.py` (immutable, defines metric) | `prepare.py` (immutable, defines metric calculation, actionability gates, hard filters) + `parameters.json` (immutable during run) |
| `train.py` (agent sandbox) | `research.py` (agent sandbox) |
| `val_bpb` (lower is better) | `actionable_pipeline_count` (higher is better) |
| 5-min training budget | 90-min experiment budget |
| `results.tsv` (5 columns, untracked) | `experiment_log.tsv` (7 columns, untracked) |
| Branch: `autoresearch/<tag>` | Branch: `autoresearch/<tag>` |
| `git reset` on failure | `git reset --hard HEAD~1` on failure |
| ~12 experiments/hour | ~16 experiments/24 hours |
| Single GPU | Single Postgres + API connectors |
| `evaluate_bpb()` is ground truth | `calculate_actionable_pipeline_count()` is ground truth |
| Simplicity criterion | Simplicity criterion |
| NEVER STOP | NEVER STOP |
| Setup phase before loop | Setup phase before loop |
| Crash → fix or skip | Crash → fix or skip |

---

## Implementation Checklist

When implementing this pattern in code, every item below must be true. If any is false, the pattern is not properly Karpathy-compliant and the metric is not trustworthy.

- [ ] `prepare.py` is in the repo and contains the metric calculation, actionability gates, hard filter logic, composite score formula, and evaluation universe definition
- [ ] `prepare.py` has a comment header stating it is immutable during a run and defining what "during a run" means
- [ ] The agent's startup prompt in `CLAUDE.md` explicitly tells it not to modify `prepare.py` or `parameters.json`
- [ ] `parameters.json` has a top-level `_immutable_during_run: true` flag
- [ ] `research.py` imports from `prepare.py` and never redefines anything imported from there
- [ ] The setup phase is implemented as a discrete sequence the agent walks through before the loop begins
- [ ] A baseline experiment is required as the first row of `experiment_log.tsv` for every new run
- [ ] The experiment loop has a hard 90-minute timeout enforced at the OS level (not just an in-Python check)
- [ ] `git reset --hard HEAD~1` is the revert mechanism, not soft reset
- [ ] Every commit on the experiment branch corresponds to a `keep` row in the TSV
- [ ] The TSV is in `.gitignore`
- [ ] The branch naming convention is `autoresearch/<tag>` and `main` is never written to during a run
- [ ] The agent does NOT have the ability to modify `prepare.py`, `parameters.json`, or `sources.json` from within `research.py` (file permissions or runtime check)
- [ ] `runner.py`, `costar_ingest.py`, `reporting.py`, and `pipeline_common.py` are treated as immutable during a run — the agent never edits the loop that evaluates its own experiments
- [ ] Every `parcel_scores` row carries `run_tag` + `experiment_id`; the metric counts only the active run's rows; a `discard`/`crash`/`timeout` decision purges that experiment's rows (the data half of the git ratchet)
- [ ] The simplicity criterion is mentioned in `program.md` and the agent's prompt
- [ ] The NEVER STOP rule is mentioned in `program.md` and the agent's prompt
- [ ] When `prepare.py` is mutated (between runs), the protocol is followed and a new branch+baseline is created
- [ ] Cross-run TSV accumulation is implemented (new run rows append to existing file)

---

## Final Note

The mechanics in this document exist to preserve metric integrity. Without them, AutoResearch is just "an agent that runs in a loop" — and an agent that runs in a loop without a trustworthy metric is just an agent that produces output you can't believe. The whole point is to wake up to results you trust.

Karpathy's three-file contract is doing real work. The immutability of `prepare.py` is what makes the autonomous loop trustworthy. Everything else is implementation detail in service of that core property.

If you find yourself wanting to modify `prepare.py` during a run "just this once" — don't. Halt the run, mutate, branch off, baseline, restart. The discipline is the point.
