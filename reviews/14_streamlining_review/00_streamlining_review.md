# Streamlining Review — Codebase and Process

> Session type: Debug/Diagnose (analysis only — no code, config, or contract files modified).
> Orientation chain Steps 1–4 completed (AUTORESEARCH_MECHANICS.md, program.md, appendix,
> infrastructure specs read in full; repo inventoried; 576 offline tests verified passing in 1.2s).
> Date: 2026-07-07. Inputs: full doc stack, all 5 Python modules, all 13 review directories,
> git history on main (58 commits), CI workflows.

---

## 0. Verdict in one paragraph

The Karpathy-pattern *design* is sound and the engineering quality where it matters most —
`prepare.py`, the test suite, CI — is genuinely high. The system's problems are (1) an
**integrity gap**: the metric's evaluation universe doesn't match the canonical spec, so the
git ratchet reverts code but not data; (2) **misplaced boundaries**: ~80–85% of the 6,660-line
"agent sandbox" is infrastructure the agent should never touch, including the experiment loop
that judges the agent's own edits; (3) **process weight**: 14,548 lines of three-agent review
markdown govern 9,047 lines of production Python, yet genuine reviewer independence existed in
only ~3 of 13 phases and the 8 single-context "self-reviews" forced zero code changes; and
(4) **documentation drift**: the orientation chain mandates reading documents that contradict
each other and the code. Each of these is fixable without abandoning the pattern — mostly by
moving boundaries, not by adding anything.

---

## 1. What is working — do not change these

| Asset | Evidence |
|---|---|
| `prepare.py` immutable metric layer | Hash-pinned parameters, deep-frozen mapping, deterministic `DISTINCT ON` latest-score selection, masked DSN, OS-level timeout primitive. Clean 742 lines. |
| Test infrastructure | 576 offline tests, 1.2s wall clock, FakeConnection pattern, fixture-driven; separate live CI jobs (PostGIS service container, live ArcGIS harness with concurrency guard). |
| CI design | Path-filtered triggers, offline/live job split so developers always get signal, env.template credential guard (hook + server-side workflow) added after the Phase 1 incident. |
| Operator ergonomics | `make daily` / halt sentinel / tmux flow; append-only TSV; advisory lock. |
| The pattern itself | Five-file contract, keep-or-revert ratchet, baseline discipline, prepare-mutation protocol. Phase 13 executed the mutation protocol correctly, including declaring metric non-comparability. |

The recommendations below are re-arrangements of what exists, not rewrites.

---

## 2. Finding A — the metric universe does not match the spec (integrity, fix first)

`AUTORESEARCH_MECHANICS.md` defines the metric as:

```
actionable_pipeline_count = COUNT(parcels WHERE ... AND scored_in_current_experiment = TRUE)
```

`prepare.py` implements **no such filter** — `calculate_actionable_pipeline_count()` counts the
latest score of *every parcel ever written to `parcel_scores`*. No column, index, or query
anywhere in the codebase references `scored_in_current_experiment` (verified by grep).

Consequences:

1. **`git reset --hard` reverts code, not data.** A `discard`/`crash` experiment's discovery
   and scoring writes persist in Postgres. The next experiment inherits them, so metric
   movement can no longer be attributed to the single change the Karpathy pattern requires.
   A discarded experiment that scored 3 new parcels above threshold permanently raises every
   subsequent measurement — the ratchet can advance on the residue of rejected work.
2. **Cross-run contamination.** A new run's baseline includes all prior runs' parcels, so
   baselines are not comparable the way the mechanics doc assumes.
3. The Phase 12 review noted `evaluate()` is non-transactional across sub-cycles (R-710), but
   that framing understates it: even a *fully successful* discarded experiment leaves its data
   behind by design.

**Recommendation A (a `prepare-mutation`, between runs, per protocol):**

- Add `run_tag TEXT` and `experiment_commit TEXT` columns to `parcel_scores` (and
  `parcels.discovered_in_run` if per-run discovery attribution is wanted).
- Redefine the metric universe in `prepare.py` to filter on the active run tag (passed
  explicitly, not read from git inside the metric), matching the spec's
  `scored_in_current_experiment` intent at run granularity — or, stricter, make
  `apply_keep_or_revert_decision` delete `parcel_scores` rows written by a discarded
  experiment's `cycle_id` so the data ratchet mirrors the git ratchet.
- Either choice is a metric-definition change: new branch, new baseline, note in the TSV.

This is the single highest-value fix in the repo. Until it lands, treat cross-experiment
metric deltas as directional, not attributable.

---

## 3. Finding B — the agent sandbox is 80–85% not-sandbox

Line-range accounting of `research.py` (6,660 lines):

| Block | Lines | % | Is it experiment surface? |
|---|---|---|---|
| CoStar ingestion ETL (Phase 6/6.1) | ~1,400 | 21% | No — frozen by COSTAR_INGESTION_CONTRACT.md |
| Snapshot/memo markdown rendering (Phase 9) | ~1,235 | 18.5% | No — output formatting |
| Experiment loop, setup checks, git/TSV plumbing (Phase 10) | ~965 | 14.5% | **No — this is the harness that judges the agent** |
| Discovery HTTP session + ArcGIS parse | ~900 | 13.5% | Mostly no (connector plumbing) |
| Module-level SQL constants | ~408 | 6% | No — must stay aligned with prepare.py DDL |
| Misc helpers (coercion, slugs, geometry, cache) | ~450 | 7% | No |
| **Scoring/discovery logic the agent actually experiments on** (H-filters, S1–S12, strategy fit, actionability gates, composite/confidence, thesis) | **~1,000–1,300** | **~15–20%** | **Yes** |

Why this matters beyond aesthetics:

- **Self-harness corruption:** the loop that evaluates experiments (`experiment_loop`,
  `evaluate`, TSV I/O, keep-or-revert) lives in the one file the agent edits freely. One bad
  edit during an experiment can silently corrupt the experiment log itself. Karpathy's
  separation of `train.py` from the runner exists precisely to prevent this.
- **Context cost per experiment:** the mechanics doc claims setup reading is "~80KB total."
  `research.py` alone is 263KB. Every experiment iteration pays this comprehension tax
  against a 90-minute budget.
- **Blast radius:** an agent tweaking S8 scoring can break CSV BOM handling three thousand
  lines away.

**Recommendation B (a one-time contract amendment, between runs):**

Split by mutability, not by phase history:

```
research.py          → the sandbox: H-filters, S1–S12, strategy fit, actionability,
                       discovery heuristics, thesis logic (~1,200 lines)
runner.py            → experiment loop, setup verification, TSV I/O, git plumbing,
                       lock/halt — IMMUTABLE during a run, governed like prepare.py
costar_ingest.py     → the ETL, frozen to the ingestion contract
reporting.py         → snapshot + memo rendering
connectors/ or lib/  → shared HTTP session, ArcGIS parsing, coercion helpers
```

Update the five-file contract table in AUTORESEARCH_MECHANICS.md to name `runner.py` (and
optionally `costar_ingest.py`) in the immutable layer. Split `tests/test_discovery.py`
(262KB — it AST-scans `research.py` internals and is the main force freezing the plumbing in
place) along the same seams. Net effect: the agent's editable world drops from 6,660 lines to
~1,200, which is the size at which the Karpathy pattern is known to work.

---

## 4. Finding C — the three-agent process costs ~1:1 against code and mostly reviews itself

Measured across all 13 review directories against `git show --stat`:

| Metric | Value |
|---|---|
| Review markdown | 14,548 lines (vs 9,047 production Python, 7,400 test lines) |
| Review floor per phase | ~600–1,200 lines regardless of change size |
| Worst ratios | Phase 4: **972 review lines for a 97-line change (~10:1)** — six constant-returning stubs. Phase 3.1: ~7:1. Phase 13: ~4.5:1. |
| Genuine three-agent independence | **~3 of 13 phases** (1, 4, 13). Eight phases (3, 3.1, 5, 6, 6.1, 7+8, 9, 10) were one orchestrator authoring all three role documents in one context — each admits this in writing. Phase 2 shipped with no Agent 3 at all (API quota). |
| Code changes forced by the 8 self-reviews | **Zero.** Every "did Agent 1 miss anything?" gap resolved "no code change needed." |
| Recycled boilerplate per risk review | ~40–55% (five-file-contract restatement, SQL-injection/AST-scanner language, cycle-id/transaction/FakeConnection "direct ports", repeated out-of-scope lists) |
| Real catches | Phase 13 (independent Agent 1): latent double-count in the metric — the highest-value catch in the corpus. Phase 3: two spec-drift bugs + a **fresh-context 4th pass** catching a wrong-ring centroid bug the self-review missed. Phase 1 Agent 3: verified the hash sentinel reads file bytes, not the dict. |
| Documented misses | Phase 2's self-review APPROVED code that needed two post-merge parsing fix-forwards (`56e4313`, `4263630`). |
| Rule applied selectively | `cli.py`, `Makefile`, devcontainer, ops targets shipped with no review directory at all. |

The pattern in this data is unambiguous: **fresh context catches bugs; ceremony does not.**
The catches came from genuinely independent contexts (Phase 13's real Agent 1, Phase 3's
independent revalidation). The 400-line templated reviewer-decision documents written by the
same context that wrote the code produced zero forced changes across eight phases.

The process also embeds itself in the product: `research.py` carries **278 risk-ID
references** (R-17, R-1301…) and 139 phase references in comments, and review decision R-17
("contractual isolation") forced ~600 lines of HTTP/retry/ArcGIS code to be *duplicated* from
`connector_harness.py` instead of shared.

**Recommendation C — replace role-count with context-independence, and tier by risk:**

1. **Create `STANDING_RISKS.md`** — one canonical checklist holding the recycled 40–55%
   (five-file contract, parameterized SQL + AST scanners, cycle-id collision, transaction
   boundaries, FakeConnection testing, PII redaction, path safety). Reviews cite line items;
   they do not re-derive them. This alone halves future review volume.
2. **Tier the process by what history shows needs it:**
   - *Tier 0 (tests + CI only):* stubs, config, docs, ops tooling — the things already being
     skipped informally. Phase 4 was Tier 0 work carrying Tier 2 ceremony.
   - *Tier 1 (one fresh-context reviewer):* ordinary `research.py` logic changes. A single
     independent review pass — a subagent or `/code-review` in a clean context — is what
     actually caught bugs; require that, not three documents.
   - *Tier 2 (full adversarial review, genuinely separate contexts):* anything touching
     `prepare.py`, the metric, keep-or-revert, new external integrations, credentials.
     This is where Phase 13 proved the value.
3. **Record deltas, not templates.** The reviewer artifact becomes a short decision record
   (gaps found, rulings, deviations) — a PR review or a 1-page file — not a 400-line
   gate-by-gate tabulation. The 28-gate hunk-by-hunk verification belongs in tests, which
   already exist and run in 1.2 seconds.
4. **Fix the stale model pin.** "All three agents must run Claude Opus 4.7" appears in the
   appendix, README, and START_HERE; it is unverifiable in 8 of 13 artifacts, and the model
   landscape has moved on (Claude 5-family models now exist). Replace with "the strongest
   available model, recorded per artifact" so the requirement is checkable and doesn't rot.
5. **Retire R-17-style isolation where it forces duplication.** Keep "the harness must not
   depend on research.py"; allow both to import a shared low-level HTTP module.

---

## 5. Finding D — documentation drift makes the mandatory orientation actively misleading

The orientation chain requires every session to read ~3,700 lines across 6+ documents and
state verbatim confirmations. Several of those confirmations are now false:

| Claim the agent must confirm / read | Reality |
|---|---|
| START_HERE Step 3: "current state is pre-Phase 0… no code written yet" | Phases 1–10 shipped; an experiment run (`autoresearch/atl-2026-05-05`) has merged `exp:` commits |
| program.md "Repository Structure": file-based `markets/`, `candidates.json`, 13-column `results.tsv`, prepare.py as "one-time setup" | Storage is Postgres; the 7-column `experiment_log.tsv` is the log; prepare.py is the metric layer. None of the listed runtime dirs exist |
| program.md + `sources.json` `_comment`: "Agent may ADD new sources" | Directly contradicts AUTORESEARCH_MECHANICS File 5 and START_HERE Anti-Pattern 7 — in the live config file the agent reads every run |
| Mechanics setup phase: "Total context: ~80KB" | Code alone is ~290KB; docs ~150KB more |
| README repo tree: `docs/diligence_program.md`, `markets/`, `rankings/`, `snapshots/` | None exist in the repo |
| `reviews/02_setup_phase/00_setup_status.md` ("current-state ledger"): "Setup phase HAS NOT begun" | A full setup + loop run happened ~5 weeks later; the ledger was never updated |
| program.md metric section: threshold "default: 70/100," four gates incl. path-to-control informational | Consistent — but the 13-column per-cycle `results.tsv` schema it mandates coexists confusingly with the 7-column experiment TSV |

**Recommendation D — one source of truth per fact, and a generated state page:**

1. **program.md v2:** delete the stale Repository Structure block (defer to README), fix the
   sources.json permission sentence, replace the `results.tsv` schema with pointers to
   `research_log` (per-action, in Postgres) and `experiment_log.tsv` (per-experiment, 7 cols).
2. **Fix `sources.json`'s `_comment`** to state the actual rule (agent read-only; humans add
   sources between experiments).
3. **Make repo state generated, not narrated.** Replace stale prose state (START_HERE Step 3's
   "pre-Phase 0", BUILD_PHASES "Prerequisites", setup_status.md as a living ledger) with
   `make status` / a tiny `STATE.md` refreshed by the loop. Point-in-time documents should be
   dated and immutable; living state should be computed.
4. **Slim the orientation chain.** Keep the 6-step chain for *run* sessions (setup, experiment,
   loop) where the confirmations guard real invariants. For spec-refinement/debug sessions,
   a 1-page ORIENTATION.md (the mutability table + anti-patterns + links) is sufficient. Most
   of the chain's value is the mutability contract, which fits in ~30 lines.
5. **README:** prune the phantom paths; state that runtime dirs are created on demand.

---

## 6. Finding E — smaller mechanical cleanups (quick wins, low risk)

1. **Latest-score selector divergence:** `prepare.py` uses `DISTINCT ON … scored_at DESC,
   score_id DESC`; `research.py`'s scoring/snapshot reads use `ORDER BY scored_at DESC LIMIT 1`
   with no `score_id` tie-break. On ties they can disagree with the metric. Reuse one selector.
2. **Live harness on every loop iteration:** `experiment_loop` → `verify_setup` → live ArcGIS
   harness checks each cycle. Cache connector health for a window (e.g., 6h, matching the
   discovery interval) instead of hammering county endpoints under NEVER STOP.
3. **`api_calls` is always 0** in the TSV (placeholder since Phase 10) — thread the counter
   from `_DiscoverySession` so the soft-constraint column carries signal.
4. **`commit="pending"` fallback** in `_git_head_commit` writes ratchet rows that can't be
   mapped to a revision; fail the row instead.
5. **Triplicated constants:** autoresearch branch regex (research.py / Makefile / cli.py),
   TSV column list (research.py / cli.py ×2). Single definition, imported.
6. **Three coercion families, three cycle-id generators, two slug helpers** inside
   research.py — collapse to one each (natural byproduct of the Recommendation B split).
7. **Broad `except Exception` in `evaluate`/memo path** downgrades real scoring bugs to
   generic `crash` rows; log tracebacks to a file referenced from the TSV notes column.
8. **cli.py vs Makefile** duplicate the same 9 operator commands; pick one as canonical
   (Makefile is the documented flow; keep cli.py only if `--json` output is actually consumed).

---

## 7. Suggested sequencing

| Order | Item | Why first | Protocol cost |
|---|---|---|---|
| 1 | **A: metric universe fix** (run-tag scoping or discard-cleanup) | Restores the attribution guarantee the whole pattern exists for | prepare-mutation → new branch + baseline |
| 2 | **D1–D3: doc drift fixes** (program.md v2, sources.json comment, generated state) | Cheap, stops every future session ingesting contradictions | docs-only, no ceremony |
| 3 | **B: research.py split** (runner/ETL/reporting out of the sandbox) | 5× smaller sandbox → faster, safer experiments; enables E5–E6 | contract amendment, between runs; tests move with code |
| 4 | **C: process re-tiering** (STANDING_RISKS.md, fresh-context reviewer, tiered depth) | Biggest recurring cost; the data shows exactly which slice pays | process docs update |
| 5 | **E: mechanical cleanups** | Small, mostly fold into the split | Tier 0/1 changes |

Items 1 and 3 change contract-layer files and therefore need the human's explicit go-ahead
and the between-runs protocol. Items 2, 4, 5 are documentation/process changes the existing
rules already exempt from heavy ceremony.

---

## 8. What this review deliberately does not recommend

- Abandoning the Karpathy pattern, the five-file contract, or prepare.py's discipline — the
  pattern is the right one; the boundaries just landed in the wrong places as the codebase
  grew 50× beyond the scaffolding the contract was written for.
- Softening the CoStar no-scraping rule, the credential hygiene, or the append-only TSV.
- Rewriting the test suite — it is the strongest asset in the repo and the reason the
  refactors above are tractable.
