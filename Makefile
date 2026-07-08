# Makefile — operator targets for the Land Research autoresearch loop.
#
# Wraps the Python public API in runner.py (Phase 10 loop) and research.py so the human
# operator does not have to type `python -c "..."` ceremonies. Pure
# ergonomic sugar — does not modify the Five-File Contract layer
# (prepare.py, parameters.json, sources.json, program.md), does not
# replace the agent's role, does not change keep-or-revert semantics.
#
# Configuration-only file per appendix_a_county_connectors.md L72-73 —
# does not require the three-agent workflow.
#
# Conventions:
#   - `## description` after a target produces `make help` output.
#   - `.DEFAULT_GOAL := help` so bare `make` shows the menu.
#   - `==>` prefix on operator-visible messages.
#   - All targets are .PHONY (none produce artifacts at the target name).
#   - `set -euo pipefail` in any multi-command recipe.

SHELL          := /bin/bash
.DEFAULT_GOAL  := help

# ---------------------------------------------------------------------
# Variables (override via `make TARGET VAR=value`)
# ---------------------------------------------------------------------
# Today's UTC date, lowercase, matches runner._AUTORESEARCH_BRANCH_RE.
TAG     ?= atl-$(shell date -u +%Y-%m-%d)
MARKET  ?= atlanta
# Empty MAX means NEVER STOP. `make loop MAX=2` caps at 2 iterations.
MAX     ?=

# ---------------------------------------------------------------------
# Help (default target)
# ---------------------------------------------------------------------
.PHONY: help
help:  ## Show this help (default target).
	@echo "Land Research — autoresearch operator targets"
	@echo ""
	@echo "Usage: make <target> [VAR=value ...]"
	@echo ""
	@echo "Variables (override with VAR=value):"
	@printf "  %-12s %s\n" "TAG"     "autoresearch tag (current default: $(TAG))"
	@printf "  %-12s %s\n" "MARKET"  "target market (current default: $(MARKET))"
	@printf "  %-12s %s\n" "MAX"     "loop max_iterations (empty = NEVER STOP)"
	@echo ""
	@echo "Targets:"
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z][a-zA-Z0-9_-]*:.*##/ \
	  { printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
	@echo ""
	@echo "DAILY OPERATOR FLOW (use this):"
	@echo "  make daily          # one command: branch + verify + tmux loop"
	@echo "  make loop-attach    # see the running loop"
	@echo "  make tail           # live TSV stream from a 2nd terminal"
	@echo "  make status         # verify_setup + last 10 TSV rows"
	@echo "  make db-stats       # per-table row counts"
	@echo "  make halt           # exit the loop cleanly"
	@echo ""
	@echo "First-run-only:"
	@echo "  make db-check       # sanity-check Supabase + PostGIS"
	@echo "  make setup          # cut autoresearch/$(TAG) from main"
	@echo "  make loop MAX=2     # bootstrap baseline + 2 iterations (foreground)"

# ---------------------------------------------------------------------
# Setup phase (AUTORESEARCH_MECHANICS.md "Setup Sequence")
# ---------------------------------------------------------------------
.PHONY: setup
setup:  ## Cut autoresearch/<TAG> from clean main, push, run verify.
	@set -euo pipefail; \
	if [ -n "$$(git status --porcelain)" ]; then \
	  echo "==> ERROR: working tree is dirty. Commit or stash first."; \
	  git status --short; \
	  exit 1; \
	fi; \
	echo "==> syncing main from origin"; \
	git checkout main; \
	git pull --ff-only origin main; \
	echo "==> cutting autoresearch/$(TAG) from main"; \
	if git show-ref --verify --quiet refs/heads/autoresearch/$(TAG); then \
	  echo "==> ERROR: autoresearch/$(TAG) already exists locally."; \
	  echo "    Use a different TAG, e.g. make setup TAG=$(TAG)-2"; \
	  exit 1; \
	fi; \
	git checkout -b autoresearch/$(TAG); \
	echo "==> pushing autoresearch/$(TAG) to origin"; \
	git push -u origin autoresearch/$(TAG); \
	echo ""; \
	echo "==> branch ready. Running verify_setup..."
	@$(MAKE) --no-print-directory verify

.PHONY: verify
verify:  ## Run verify_setup(MARKET) and pretty-print the result.
	@python -c "import json, runner; \
	print(json.dumps(runner.verify_setup('$(MARKET)'), indent=2, default=str))"

.PHONY: db-check
db-check:  ## Run python prepare.py — Supabase + PostGIS sanity ping.
	@python prepare.py

# ---------------------------------------------------------------------
# Loop control
# ---------------------------------------------------------------------
.PHONY: _assert-autoresearch-branch
_assert-autoresearch-branch:
	@branch=$$(git rev-parse --abbrev-ref HEAD); \
	if ! echo "$$branch" | grep -qE '^autoresearch/[a-z0-9._-]+$$'; then \
	  echo "==> ERROR: current branch '$$branch' is not autoresearch/<tag>."; \
	  echo "    Run 'make setup' first, or 'git checkout autoresearch/<tag>'."; \
	  exit 1; \
	fi

.PHONY: baseline
baseline: _assert-autoresearch-branch  ## Run the baseline experiment for MARKET.
	@echo "==> running baseline experiment for market=$(MARKET)"
	@python -c "import json, runner; \
	row = runner.run_baseline_experiment('$(MARKET)'); \
	print(json.dumps(row, indent=2, default=str))"
	@echo ""
	@echo "==> baseline row appended to experiment_log.tsv:"
	@$(MAKE) --no-print-directory log

.PHONY: loop
loop: _assert-autoresearch-branch  ## Run experiment_loop. NEVER STOP unless MAX is set.
	@if [ -f .halt ]; then \
	  echo "==> WARNING: .halt sentinel exists — the loop will exit immediately."; \
	  echo "    Run 'make unhalt' first to clear it."; \
	  exit 1; \
	fi
	@echo "==> starting experiment_loop(market=$(MARKET), max_iterations=$(if $(MAX),$(MAX),None))"
	@echo "==> halt with: 'make halt' from another shell, or set EXPERIMENT_LOOP_HALT=1"
	@MAX="$(MAX)" python -c "import os, runner; \
m = os.environ.get('MAX', '').strip(); \
mi = int(m) if m else None; \
summary = runner.experiment_loop('$(MARKET)', max_iterations=mi, confirmed=True); \
print(summary)"

.PHONY: halt
halt:  ## Touch .halt — the running loop exits on next iteration boundary.
	@if [ -f .halt ]; then \
	  echo "==> .halt already exists ($(shell ls -l .halt 2>/dev/null | awk '{print $$6, $$7, $$8}'))"; \
	else \
	  touch .halt; \
	  echo "==> .halt sentinel created. The loop will exit on its next iteration boundary."; \
	fi

.PHONY: unhalt
unhalt:  ## Remove .halt — required before starting a new loop run.
	@rm -f .halt
	@echo "==> .halt sentinel removed."

# ---------------------------------------------------------------------
# Inspection (read-only)
# ---------------------------------------------------------------------
.PHONY: orient
orient:  ## Offline orientation snapshot: contract card + branch/run/TSV state (no DB).
	@echo "==> contract card: CLAUDE.md (invariants + how much orientation this session needs)"
	@echo ""
	@echo "==> current branch: $$(git rev-parse --abbrev-ref HEAD)"
	@echo ""
	@echo "==> autoresearch branches (most recent first):"
	@branches=$$(git branch --list 'autoresearch/*' --sort=-committerdate --format='  %(refname:short)  (%(committerdate:relative))'); \
	if [ -n "$$branches" ]; then echo "$$branches"; else echo "  (none — no runs started yet)"; fi
	@echo ""
	@if [ -f experiment_log.tsv ]; then \
	  echo "==> last 5 rows of experiment_log.tsv:"; \
	  (head -1 experiment_log.tsv; tail -5 experiment_log.tsv | grep -v '^commit\b' || true) \
	    | column -t -s "$$(printf '\t')"; \
	else \
	  echo "==> no experiment_log.tsv yet (no baseline recorded — run 'make baseline' or 'make loop')"; \
	fi
	@if [ -f .halt ]; then echo ""; echo "==> NOTE: .halt sentinel is set"; fi

.PHONY: status
status:  ## Print verify_setup + last 10 rows of experiment_log.tsv.
	@echo "==> verify_setup(market=$(MARKET))"
	@python -c "import json, runner; \
	print(json.dumps(runner.verify_setup('$(MARKET)'), indent=2, default=str))"
	@echo ""
	@if [ -f experiment_log.tsv ]; then \
	  echo "==> last 10 rows of experiment_log.tsv:"; \
	  (head -1 experiment_log.tsv; tail -10 experiment_log.tsv | grep -v '^commit\b' || true) \
	    | column -t -s "$$(printf '\t')"; \
	else \
	  echo "==> experiment_log.tsv does not exist yet (run 'make baseline' or 'make loop')"; \
	fi
	@if [ -f .halt ]; then echo ""; echo "==> NOTE: .halt sentinel is set"; fi

.PHONY: log
log:  ## Pretty-print full experiment_log.tsv as an aligned table.
	@if [ -f experiment_log.tsv ]; then \
	  column -t -s "$$(printf '\t')" experiment_log.tsv; \
	else \
	  echo "==> no experiment_log.tsv yet"; \
	fi

.PHONY: tail
tail:  ## tail -f experiment_log.tsv for live monitoring (Ctrl-C to exit).
	@if [ ! -f experiment_log.tsv ]; then \
	  echo "==> waiting for experiment_log.tsv to be created..."; \
	  while [ ! -f experiment_log.tsv ]; do sleep 1; done; \
	fi
	@tail -f experiment_log.tsv

.PHONY: db-stats
db-stats:  ## Per-table row counts (parcels, parcel_scores, research_log, flagged_items).
	@python -c "\
import prepare; \
conn_ctx = prepare.get_connection(); conn = conn_ctx.__enter__(); \
cur = conn.cursor(); \
queries = [ \
    ('parcels',                  'SELECT COUNT(*) FROM parcels'), \
    ('parcel_scores',            'SELECT COUNT(*) FROM parcel_scores'), \
    ('parcel_scores actionable', \"SELECT COUNT(*) FROM parcel_scores WHERE actionability='PASS' AND composite_score >= 70\"), \
    ('research_log',             'SELECT COUNT(*) FROM research_log'), \
    ('flagged_items',            'SELECT COUNT(*) FROM flagged_items'), \
    ('submarkets',               'SELECT COUNT(*) FROM submarkets'), \
]; \
[print(f'{n:30s}{cur.execute(q) or cur.fetchone()[0]}') for n, q in queries]; \
print(); \
cur.execute(\"SELECT action_type, COUNT(*) FROM research_log GROUP BY action_type ORDER BY 2 DESC\"); \
print('research_log by action_type:'); \
[print(f'  {a:25s}{n}') for a, n in cur.fetchall()]; \
conn_ctx.__exit__(None, None, None)"

# ---------------------------------------------------------------------
# Background / tmux loop control (R-733 ergonomic)
# ---------------------------------------------------------------------
.PHONY: loop-bg
loop-bg: _assert-autoresearch-branch  ## Start the loop in a detached tmux session named 'loop'.
	@if ! command -v tmux >/dev/null; then \
	  echo "==> tmux not installed. Run: sudo apt-get install -y tmux"; \
	  exit 1; \
	fi
	@if tmux has-session -t loop 2>/dev/null; then \
	  echo "==> tmux session 'loop' already exists. Attach with 'make loop-attach' or kill with 'tmux kill-session -t loop'."; \
	  exit 1; \
	fi
	@if [ -f .halt ]; then \
	  echo "==> WARNING: .halt sentinel exists. Run 'make unhalt' first."; \
	  exit 1; \
	fi
	@tmux new-session -d -s loop \
	  "cd $(CURDIR) && make loop $(if $(MAX),MAX=$(MAX),) 2>&1 | tee /tmp/loop-$$$$.log; echo; echo 'loop ended -- press any key'; read -n 1"
	@echo "==> loop started in detached tmux session 'loop'"
	@echo "==> attach:  make loop-attach"
	@echo "==> tail:    make tail"
	@echo "==> halt:    make halt"
	@if [ -n "$${CODESPACES:-}" ]; then \
	  echo ""; \
	  echo "==> WARNING: this is a GitHub Codespace. Codespaces STOP on idle"; \
	  echo "    (default 30 min after you disconnect; max 240 min). A detached"; \
	  echo "    tmux session does NOT keep the machine alive, so an overnight"; \
	  echo "    run will be cut short. Bump the timeout (Settings -> Codespaces"; \
	  echo "    or 'gh codespace edit --idle-timeout 240m') or use a durable"; \
	  echo "    host for multi-hour runs. See README 'Overnight runs'."; \
	fi

.PHONY: loop-attach
loop-attach:  ## Attach to the running 'loop' tmux session (Ctrl-B d to detach).
	@if ! tmux has-session -t loop 2>/dev/null; then \
	  echo "==> no tmux session named 'loop'. Start one with 'make loop-bg'."; \
	  exit 1; \
	fi
	@tmux attach -t loop

# ---------------------------------------------------------------------
# Durability mirror (reviews/17_tsv_mirror — the TSV remains canonical)
# ---------------------------------------------------------------------
.PHONY: mirror-backfill
mirror-backfill:  ## Reconcile experiment_log.tsv -> Postgres mirror (idempotent).
	@python -c "import json, runner; \
	print(json.dumps(runner.backfill_experiment_log_mirror(), indent=2))"

.PHONY: mirror-restore
mirror-restore:  ## Disaster recovery: rebuild a MISSING experiment_log.tsv from the mirror.
	@python -c "import json, runner; \
	print(json.dumps(runner.restore_experiment_log_from_mirror(), indent=2))"

# ---------------------------------------------------------------------
# Higher-level recipes
# ---------------------------------------------------------------------
.PHONY: resume
resume:  ## Switch to today's autoresearch branch (or the most recent) and pull.
	@set -euo pipefail; \
	target=$$(git branch --list 'autoresearch/*' --sort=-committerdate --format='%(refname:short)' | head -1); \
	if [ -z "$$target" ]; then \
	  echo "==> no autoresearch/* branches exist locally. Did you run 'make setup'?"; \
	  exit 1; \
	fi; \
	echo "==> resuming on $$target"; \
	git checkout "$$target"; \
	git pull origin "$$target"
	@$(MAKE) --no-print-directory db-stats

.PHONY: daily
daily:  ## ONE COMMAND: cut/resume autoresearch/<TAG>, verify infra, kick loop in tmux.
	@set -euo pipefail; \
	branch=$$(git rev-parse --abbrev-ref HEAD); \
	target="autoresearch/$(TAG)"; \
	if [ "$$branch" != "$$target" ]; then \
	  if git show-ref --verify --quiet "refs/heads/$$target"; then \
	    echo "==> autoresearch/$(TAG) already exists locally; switching"; \
	    git checkout "$$target"; \
	    git pull --ff-only origin "$$target" || true; \
	  else \
	    echo "==> cutting autoresearch/$(TAG) from main"; \
	    $(MAKE) --no-print-directory setup; \
	  fi; \
	else \
	  echo "==> already on $$target"; \
	  git pull --ff-only origin "$$target" || true; \
	fi
	@if [ -f .devcontainer/post-start.sh ] && [ -n "$${DATABASE_URL:-}" ] && [ ! -f .env ]; then \
	  bash .devcontainer/post-start.sh; \
	fi
	@$(MAKE) --no-print-directory db-check
	@$(MAKE) --no-print-directory loop-bg

# ---------------------------------------------------------------------
# Tests + dev hygiene
# ---------------------------------------------------------------------
.PHONY: tests
tests:  ## Run the offline test suite (unittest, no network).
	@python -m unittest discover tests

.PHONY: clean-runtime
clean-runtime:  ## Remove runtime artifacts (snapshots, rankings, .halt). Preserves TSV.
	@rm -rf snapshots rankings harness_reports sources flagged
	@rm -f .halt
	@echo "==> cleared snapshots/ rankings/ harness_reports/ sources/ flagged/ and .halt"
	@echo "==> experiment_log.tsv preserved"
