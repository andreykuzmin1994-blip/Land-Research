#!/usr/bin/env bash
# Land Research autopilot for an always-on home machine (WSL Ubuntu, or any
# Linux/macOS box). The Windows .bat files in home_machine/windows/ call it.
#
#   autopilot.sh configure <settings.txt>       write .env, check the database
#   autopilot.sh start <settings.txt> [--new-run]
#                                               start or resume the run, then
#                                               launch the loop in tmux
#   autopilot.sh loop                           (inside tmux) the experiment loop
#   autopilot.sh stop                           make halt: stop after the
#                                               current experiment
#   autopilot.sh status                         make status + recent autopilot log
#
# One experiment per round, per AUTORESEARCH_MECHANICS.md "The Experiment Loop":
#   1. Claude Code (claude -p, fresh context) reads the history and commits ONE
#      change to research.py as "exp: ...". It never runs the experiment.
#   2. This script refuses anything else: a commit touching another file, more
#      than one commit, or uncommitted edits are thrown away unevaluated.
#   3. `python cli.py loop --max 1` evaluates it (runner.py, immutable) and
#      appends the TSV row with the keep-or-revert decision.
#   4. keep -> push the branch; discard/crash/timeout -> git reset --hard back.
# The runner never runs git itself (R-723); step 4 is the agent's half of the
# ratchet, done here mechanically so nothing depends on the agent doing it.
#
# Halts: the .halt sentinel (make halt / Stop.bat), 7 days of running,
# 5 crash/timeout experiments in a row, or the database/harness failing for
# more than an hour (AUTORESEARCH_MECHANICS.md "What Manually Halted Means").

set -uo pipefail

REPO="${LAND_RESEARCH_REPO:-$HOME/Land-Research}"
STATE="$HOME/.land-research"
LOG="$STATE/autopilot.log"
SESSION=landresearch
SEVEN_DAYS=$((7 * 24 * 3600))
EXPERIMENT_BACKSTOP=120m     # runner enforces 90 min itself; this only catches hangs
AGENT_BUDGET=45m
COSTAR_TYPES="submarket_stats land_sales_comps building_sales_comps leasing_comps land_listings"

export PATH="$HOME/.local/bin:$PATH"
mkdir -p "$STATE/agent-logs" "$STATE/bin"

say() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG"; }
die() { say "ERROR: $*"; exit 1; }

# --- settings ---------------------------------------------------------------

# KEY=VALUE lines; blank lines and # comments ignored; Windows line endings ok.
setting() {
    local file=$1 key=$2
    sed -e 's/\r$//' "$file" | grep -E "^[[:space:]]*$key[[:space:]]*=" | tail -1 \
        | sed -E "s/^[[:space:]]*$key[[:space:]]*=[[:space:]]*//; s/[[:space:]]+$//"
}

# Writes .env (DATABASE_URL, mode 600) and $STATE/settings.env (no secrets).
# Never prints the connection string.
configure() {
    local file=$1 db costar market
    [ -f "$file" ] || die "settings file not found: $file"
    db=$(setting "$file" DATABASE_URL)
    costar=$(setting "$file" COSTAR_FOLDER)
    market=$(setting "$file" MARKET)
    [ -n "$market" ] || market=atlanta
    if [ -n "$db" ]; then
        case "$db" in
            postgresql://*|postgres://*) ;;
            *) die "DATABASE_URL in settings.txt must start with postgresql://" ;;
        esac
        case "$db" in
            *:6543/*) die "DATABASE_URL uses port 6543 (transaction pooler). Use the Session pooler (port 5432)." ;;
        esac
        ( umask 077; printf 'DATABASE_URL=%s\n' "$db" > "$REPO/.env.tmp" ) \
            && mv "$REPO/.env.tmp" "$REPO/.env"
    elif [ ! -f "$REPO/.env" ]; then
        die "DATABASE_URL is empty in settings.txt"
    fi
    {
        printf 'MARKET=%s\n' "$market"
        printf 'COSTAR_FOLDER=%s\n' "$costar"
    } > "$STATE/settings.env"
}

load_state_settings() {
    MARKET=$(setting "$STATE/settings.env" MARKET)
    COSTAR_FOLDER=$(setting "$STATE/settings.env" COSTAR_FOLDER)
    [ -n "$MARKET" ] || MARKET=atlanta
}

enter_repo() {
    cd "$REPO" || die "repository not found at $REPO (run Install.bat)"
    [ -f .venv/bin/activate ] || die "no .venv in $REPO (run Install.bat)"
    # shellcheck disable=SC1091
    . .venv/bin/activate
}

# --- CoStar -----------------------------------------------------------------

# Copies new CSVs from the Windows CoStar folder into costar_exports/<type>/.
# The runner ingests them at the start of the next experiment (runner.evaluate
# -> costar_ingest.run_ingestion_cycle). Files are routed by name prefix.
sync_costar() {
    local src f name lower key type t seen="$STATE/costar_synced.txt"
    [ -n "${COSTAR_FOLDER:-}" ] || return 0
    case "$COSTAR_FOLDER" in
        [A-Za-z]:\\*|[A-Za-z]:/*) src=$(wslpath -u "$COSTAR_FOLDER" 2>/dev/null) ;;
        *) src=$COSTAR_FOLDER ;;
    esac
    if [ -z "$src" ] || [ ! -d "$src" ]; then
        say "CoStar folder not found: $COSTAR_FOLDER"
        return 0
    fi
    touch "$seen"
    for f in "$src"/*; do
        [ -f "$f" ] || continue
        name=$(basename "$f")
        lower=$(printf '%s' "$name" | tr '[:upper:]' '[:lower:]')
        case "$lower" in *.csv) ;; *) continue ;; esac
        key="$name|$(stat -c '%s.%Y' "$f")"
        grep -qxF "$key" "$seen" && continue
        type=""
        for t in $COSTAR_TYPES; do
            case "$lower" in "${t}"_*) type=$t ;; esac
        done
        if [ -z "$type" ]; then
            say "CoStar: skipped $name (name must start with one of: $COSTAR_TYPES)"
        else
            mkdir -p "costar_exports/$type"
            cp -p "$f" "costar_exports/$type/$name" && say "CoStar: queued $name for ingestion"
        fi
        printf '%s\n' "$key" >> "$seen"
    done
}

# --- git / TSV helpers --------------------------------------------------------

tsv_rows() { [ -f experiment_log.tsv ] && wc -l < experiment_log.tsv || echo 0; }
tsv_last() { tail -n 1 experiment_log.tsv | cut -f"$1"; }   # 1=commit 6=status
head_short() { git rev-parse --short=7 HEAD; }

# An "exp:" commit at HEAD with no TSV row was never evaluated (power cut,
# reboot, kill mid-experiment). Drop it so the next round starts clean.
recover_unevaluated() {
    local subject
    subject=$(git log -1 --format=%s)
    case "$subject" in
        exp:*)
            if ! cut -f1 experiment_log.tsv 2>/dev/null | grep -qxF "$(head_short)"; then
                say "dropping unevaluated experiment commit $(head_short): $subject"
                git reset -q --hard HEAD~1
            fi ;;
    esac
}

push_branch() {
    local i
    for i in 1 2 3; do
        git push -q origin HEAD 2>>"$LOG" && return 0
        sleep $((i * 10))
    done
    say "warning: could not push $(git rev-parse --abbrev-ref HEAD) (kept locally)"
}

has_baseline() {
    python -c "import runner, sys; sys.exit(0 if runner.current_run_has_baseline() else 1)" 2>/dev/null
}

# --- start ----------------------------------------------------------------

new_run() {
    local base tag n=2
    [ -z "$(git status --porcelain)" ] || die "the repository has uncommitted changes; cannot start a new run"
    git fetch -q origin
    base="atl-$(date -u +%Y-%m-%d)"
    tag=$base
    while git show-ref --verify --quiet "refs/heads/autoresearch/$tag" \
        || git ls-remote --exit-code --heads origin "autoresearch/$tag" >/dev/null 2>&1; do
        tag="$base-$n"; n=$((n + 1))
    done
    say "starting new run autoresearch/$tag"
    make --no-print-directory setup TAG="$tag" MARKET="$MARKET" 2>&1 | tee -a "$LOG"
    [ "${PIPESTATUS[0]}" -eq 0 ] || die "make setup failed"
    printf '%s\n' "$tag" > "$STATE/run_tag"
}

confirm_baseline() {
    local ans
    if ! has_baseline; then
        say "recording this run's baseline (research.py unmodified; can take up to 90 minutes)"
        make --no-print-directory baseline MARKET="$MARKET" 2>&1 | tee -a "$LOG"
        [ "${PIPESTATUS[0]}" -eq 0 ] || die "baseline failed; see the messages above"
    fi
    # Setup Step 6: a human confirms the baseline before the loop begins.
    if [ ! -f "$STATE/confirmed_$(cat "$STATE/run_tag")" ]; then
        echo
        echo "Baseline for this run:"
        python cli.py log --tail 1
        echo
        read -r -p "Start the experiment loop on this baseline? Type yes to start: " ans
        [ "$ans" = yes ] || { say "loop not started (baseline not confirmed)"; exit 2; }
        touch "$STATE/confirmed_$(cat "$STATE/run_tag")"
    fi
}

start() {
    local settings=$1 new=${2:-} tag
    configure "$settings"
    load_state_settings
    enter_repo
    if tmux has-session -t "$SESSION" 2>/dev/null; then
        say "the loop is already running"
        exit 0
    fi
    tag=$(cat "$STATE/run_tag" 2>/dev/null)
    if [ "$new" = "--new-run" ] || [ -z "$tag" ] \
        || ! git show-ref --verify --quiet "refs/heads/autoresearch/$tag"; then
        new_run
    else
        git checkout -q "autoresearch/$tag" || die "cannot switch to autoresearch/$tag"
        say "resuming run autoresearch/$tag"
    fi
    recover_unevaluated
    sync_costar
    confirm_baseline
    python cli.py unhalt >/dev/null 2>&1
    cp "$REPO/home_machine/autopilot.sh" "$REPO/home_machine/agent_prompt.md" "$STATE/bin/"
    tmux new-session -d -s "$SESSION" "bash '$STATE/bin/autopilot.sh' loop; echo; echo 'Autopilot stopped. Press Enter to close.'; read -r _"
    say "loop started in tmux session '$SESSION'"
}

# --- the loop ---------------------------------------------------------------

run_agent() {
    local out="$STATE/agent-logs/$(date +%Y%m%d-%H%M%S).log"
    local allowed=(
        "Read" "Grep" "Glob" "Edit" "Write"
        "Bash(git add research.py)" "Bash(git commit:*)" "Bash(git restore research.py)"
        "Bash(git log:*)" "Bash(git diff:*)" "Bash(git status:*)" "Bash(git show:*)"
        "Bash(python -m unittest:*)" "Bash(python cli.py log:*)" "Bash(make orient)"
    )
    local denied=(
        "Bash(git push:*)" "Bash(git reset:*)" "Bash(git checkout:*)" "Bash(git switch:*)"
        "Bash(git rebase:*)" "Bash(make loop:*)" "Bash(make baseline:*)" "Bash(python cli.py loop:*)"
        "Bash(python cli.py baseline:*)"
    )
    local f
    for f in prepare.py parameters.json sources.json program.md runner.py \
             costar_ingest.py reporting.py pipeline_common.py; do
        denied+=("Edit($f)" "Write($f)")
    done
    say "agent: proposing the next experiment (log: $out)"
    timeout "$AGENT_BUDGET" claude -p "$(cat "$STATE/bin/agent_prompt.md")" \
        --permission-mode acceptEdits \
        --allowedTools "${allowed[@]}" \
        --disallowedTools "${denied[@]}" \
        --max-turns 80 > "$out" 2>&1
    tail -n 3 "$out" | sed 's/^/    agent: /' | tee -a "$LOG" >/dev/null
}

loop() {
    local started noop=0 terminal=0 infra_since="" pre n files subject before rc status commit
    load_state_settings
    enter_repo
    started=$(date +%s)
    say "autopilot loop started on $(git rev-parse --abbrev-ref HEAD) (market=$MARKET)"
    while :; do
        if [ -f .halt ]; then say "halt requested; autopilot stopped"; return 0; fi
        if [ $(( $(date +%s) - started )) -gt "$SEVEN_DAYS" ]; then
            say "7 days of running reached; stopping. Use Start New Run for the next run."
            python cli.py halt >/dev/null; return 0
        fi
        sync_costar
        recover_unevaluated
        pre=$(git rev-parse HEAD)

        run_agent
        if [ -n "$(git status --porcelain)" ]; then
            say "agent left uncommitted changes; discarding them"
            git reset -q --hard HEAD
        fi
        n=$(git rev-list --count "$pre"..HEAD)
        if [ "$n" -eq 0 ]; then
            noop=$((noop + 1))
            say "agent made no experiment commit ($noop in a row)"
            sleep $(( noop >= 3 ? 1800 : 60 ))
            continue
        fi
        noop=0
        files=$(git diff --name-only "$pre" HEAD)
        subject=$(git log -1 --format=%s)
        if [ "$n" -ne 1 ] || [ "$files" != "research.py" ] || [ "${subject#exp:}" = "$subject" ]; then
            say "rejected: an experiment is ONE 'exp:' commit touching only research.py (got $n commit(s): $(echo $files))"
            git reset -q --hard "$pre"
            continue
        fi

        say "experiment $(head_short): $subject"
        before=$(tsv_rows)
        timeout --signal=INT --kill-after=60 "$EXPERIMENT_BACKSTOP" \
            python cli.py loop --market "$MARKET" --max 1 >> "$LOG" 2>&1
        rc=$?
        status=""; commit=""
        if [ "$(tsv_rows)" -gt "$before" ]; then
            status=$(tsv_last 6); commit=$(tsv_last 1)
        fi

        if [ "$rc" -ne 0 ] || [ -z "$status" ] || [ "$status" = halt ]; then
            git reset -q --hard "$pre"
            if [ -f .halt ]; then say "halt requested; autopilot stopped"; return 0; fi
            [ -n "$infra_since" ] || infra_since=$(date +%s)
            say "experiment did not run (exit $rc, row '${status:-none}'): database, harness or setup problem; retrying in 10 minutes"
            if [ $(( $(date +%s) - infra_since )) -gt 3600 ]; then
                say "infrastructure failing for over an hour; halting. Check 'make status', then Start again."
                python cli.py halt >/dev/null; return 1
            fi
            sleep 600
            continue
        fi
        infra_since=""

        if [ "$commit" != "$(head_short)" ]; then
            say "warning: TSV row commit $commit != HEAD $(head_short); reverting to be safe"
            git reset -q --hard "$pre"
            continue
        fi
        case "$status" in
            keep|baseline)
                terminal=0
                say "KEEP $(head_short) (metric $(tsv_last 2)); pushing"
                push_branch ;;
            discard)
                terminal=0
                say "DISCARD $(head_short) (metric $(tsv_last 2)); reverted"
                git reset -q --hard "$pre" ;;
            crash|timeout)
                terminal=$((terminal + 1))
                say "${status^^} $(head_short); reverted ($terminal in a row)"
                git reset -q --hard "$pre"
                if [ "$terminal" -ge 5 ]; then
                    say "5 crash/timeout experiments in a row; halting. Check the log, then Start again."
                    python cli.py halt >/dev/null; return 1
                fi ;;
            *)
                say "unexpected status '$status'; reverting"
                git reset -q --hard "$pre" ;;
        esac
    done
}

# --- entry ------------------------------------------------------------------

cmd=${1:-}
case "$cmd" in
    configure)
        configure "${2:?settings file}"
        enter_repo
        say "checking the database connection"
        python prepare.py >/dev/null 2>>"$LOG" && say "database OK" \
            || die "could not connect to the database; check DATABASE_URL in settings.txt (details in $LOG)" ;;
    start) start "${2:?settings file}" "${3:-}" ;;
    loop) loop ;;
    stop)
        cd "$REPO" && touch .halt
        say "halt requested: the loop stops after the current experiment" ;;
    status)
        enter_repo
        if tmux has-session -t "$SESSION" 2>/dev/null; then echo "Loop: RUNNING"; else echo "Loop: stopped"; fi
        echo "Run: autoresearch/$(cat "$STATE/run_tag" 2>/dev/null || echo '(none yet)')"
        echo
        python cli.py log --tail 15 2>/dev/null || echo "(no experiments yet)"
        echo
        echo "Recent autopilot activity:"
        tail -n 15 "$LOG" 2>/dev/null ;;
    *)
        echo "usage: $0 {configure <settings>|start <settings> [--new-run]|loop|stop|status}" >&2
        exit 64 ;;
esac
