#!/usr/bin/env bash
# Land Research autopilot for an always-on home machine (WSL Ubuntu, or any
# Linux/macOS box). The Windows .bat files in home_machine/windows/ call it.
#
#   autopilot.sh configure <settings.txt>       write .env, check the database
#   autopilot.sh start <settings.txt> [--new-run|--autostart]
#                                               start or resume the run, then
#                                               launch the loop in tmux
#   autopilot.sh loop                           (inside tmux) the experiment loop
#   autopilot.sh stop                           make halt: stop after the
#                                               current experiment
#   autopilot.sh status                         make status + recent autopilot log
#
# One experiment per round, per AUTORESEARCH_MECHANICS.md "The Experiment Loop":
#   1. Claude Code (claude -p, fresh context) reads the history and commits ONE
#      change to research.py as "exp: ...". It may edit only research.py and
#      never runs the experiment.
#   2. This script refuses anything else: a commit touching another file, more
#      than one commit, an amended commit, or leftover edits are thrown away
#      unevaluated.
#   3. `python cli.py loop --max 1` evaluates it (runner.py, immutable) and
#      appends the TSV row with the keep-or-revert decision.
#   4. keep -> push the branch; discard/crash/timeout -> git reset --hard back.
# The runner never runs git itself (R-723); step 4 is the agent's half of the
# ratchet, done here mechanically so nothing depends on the agent doing it.
#
# Halts: the .halt sentinel (Stop.bat), 7 days since the run started,
# 5 crash/timeout experiments in a row, Claude Code failing 5 rounds in a row,
# or the database/harness failing for more than an hour
# (AUTORESEARCH_MECHANICS.md "What Manually Halted Means"). Only a human
# clears a halt: Start does, the Windows auto-start (--autostart) does not.

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

# Sets DATABASE_URL in .env (mode 600), keeping any other lines already there,
# and writes $STATE/settings.env (no secrets). Never prints the connection string.
configure() {
    local file=$1 db costar market tmp
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
        tmp="$REPO/.env.tmp"
        (
            umask 077
            { [ -f "$REPO/.env" ] && grep -v -E '^[[:space:]]*DATABASE_URL[[:space:]]*=' "$REPO/.env"
              printf 'DATABASE_URL=%s\n' "$db"; } > "$tmp"
        ) && mv "$tmp" "$REPO/.env" && chmod 600 "$REPO/.env"
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
tsv_last() { tail -n 1 experiment_log.tsv | cut -f"$1"; }   # 1=commit 2=metric 6=status
head_short() { git rev-parse --short=7 HEAD; }
tsv_hash() { [ -f experiment_log.tsv ] && sha256sum < experiment_log.tsv || echo none; }

# True when the TSV has a keep or baseline row for commit $1.
kept_in_tsv() {
    [ -f experiment_log.tsv ] || return 1
    awk -F'\t' -v c="$1" '$1 == c && ($6 == "keep" || $6 == "baseline") { f = 1 } END { exit !f }' \
        experiment_log.tsv
}

# Back to a clean tree at HEAD: drops leftover edits and untracked files from a
# killed round, and a stale index lock from a git process killed mid-write.
clean_tree() {
    if [ -f .git/index.lock ] && ! pgrep -x git >/dev/null; then
        rm -f .git/index.lock
    fi
    git reset -q --hard HEAD
    git clean -fdq
}

# An "exp:" commit at HEAD that the TSV does not record as kept was either
# never evaluated (power cut, reboot, kill mid-experiment) or was rejected and
# not yet reverted when the process died. Drop it so the next round starts
# from the last kept code.
recover_unkept() {
    local subject
    subject=$(git log -1 --format=%s)
    case "$subject" in
        exp:*)
            if ! kept_in_tsv "$(head_short)"; then
                say "dropping experiment commit $(head_short) that was not kept: $subject"
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

run_tag() { cat "$STATE/run_tag" 2>/dev/null; }

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
    local ans tag
    tag=$(run_tag)
    if ! has_baseline; then
        say "recording this run's baseline (research.py unmodified; can take up to 90 minutes)"
        make --no-print-directory baseline MARKET="$MARKET" 2>&1 | tee -a "$LOG"
        [ "${PIPESTATUS[0]}" -eq 0 ] || die "baseline failed; see the messages above"
    fi
    # Setup Step 6: a human confirms the baseline before the loop begins.
    if [ ! -f "$STATE/confirmed_$tag" ]; then
        echo
        echo "Baseline for this run:"
        python cli.py log --tail 1
        echo
        read -r -p "Start the experiment loop on this baseline? Type yes to start: " ans
        [ "$ans" = yes ] || { say "loop not started (baseline not confirmed)"; exit 2; }
        touch "$STATE/confirmed_$tag"
    fi
    # The 7-day limit counts from the run's start, across restarts.
    [ -f "$STATE/run_started_$tag" ] || date +%s > "$STATE/run_started_$tag"
}

start() {
    local settings=$1 flag=${2:-} tag
    configure "$settings"
    load_state_settings
    enter_repo
    if tmux has-session -t "$SESSION" 2>/dev/null; then
        say "the loop is already running"
        exit 0
    fi
    tag=$(run_tag)
    if [ "$flag" = "--autostart" ]; then
        # Windows sign-in: resume only a confirmed run that a human did not stop.
        if [ -f .halt ]; then say "auto-start skipped: the loop was stopped with Stop. Use Start to resume."; exit 0; fi
        if [ -z "$tag" ] || [ ! -f "$STATE/confirmed_$tag" ]; then
            say "auto-start skipped: no confirmed run yet. Use Start to set one up."; exit 0
        fi
    fi
    if [ "$flag" = "--new-run" ] || [ -z "$tag" ] \
        || ! git show-ref --verify --quiet "refs/heads/autoresearch/$tag"; then
        new_run
    else
        git checkout -q "autoresearch/$tag" || die "cannot switch to autoresearch/$tag"
        clean_tree
        say "resuming run autoresearch/$tag"
    fi
    recover_unkept
    sync_costar
    confirm_baseline
    python cli.py unhalt >/dev/null 2>&1
    cp "$REPO/home_machine/autopilot.sh" "$REPO/home_machine/agent_prompt.md" "$STATE/bin/"
    tmux new-session -d -s "$SESSION" "bash '$STATE/bin/autopilot.sh' loop"
    say "loop started in tmux session '$SESSION'"
}

# --- the loop ---------------------------------------------------------------

# Runs Claude Code for one round. Only research.py may be edited; .env is
# unreadable to it. Returns claude's exit code.
run_agent() {
    local out rc
    out="$STATE/agent-logs/$(date +%Y%m%d-%H%M%S).log"
    local allowed=(
        "Read" "Grep" "Glob" "Edit(./research.py)" "Write(./research.py)"
        "Bash(git add research.py)" "Bash(git commit -m:*)" "Bash(git restore research.py)"
        "Bash(git log:*)" "Bash(git diff)" "Bash(git diff research.py)" "Bash(git status)"
        "Bash(git show:*)" "Bash(python -m unittest discover -s tests)" "Bash(python cli.py log:*)"
    )
    local denied=(
        "Read(./.env)" "Read(./.env.*)" "Bash(git diff --no-index:*)"
        "Bash(git push:*)" "Bash(git reset:*)" "Bash(git checkout:*)" "Bash(git switch:*)"
        "Bash(git rebase:*)" "Bash(git commit --amend:*)"
    )
    say "agent: proposing the next experiment (log: $out)"
    timeout "$AGENT_BUDGET" claude -p "$(cat "$STATE/bin/agent_prompt.md")" \
        --allowedTools "${allowed[@]}" \
        --disallowedTools "${denied[@]}" \
        --max-turns 80 > "$out" 2>&1
    rc=$?
    tail -n 3 "$out" | sed 's/^/    agent: /' >> "$LOG"
    return "$rc"
}

loop() {
    local started tag noop=0 agent_fail=0 terminal=0 infra_since="" retry=""
    local pre n files subject before rc arc status commit tsv_before
    load_state_settings
    enter_repo
    tag=$(run_tag)
    started=$(cat "$STATE/run_started_$tag" 2>/dev/null || date +%s)
    say "autopilot loop started on $(git rev-parse --abbrev-ref HEAD) (market=$MARKET)"
    while :; do
        if [ -f .halt ]; then say "halt requested; autopilot stopped"; return 0; fi
        if [ $(( $(date +%s) - started )) -gt "$SEVEN_DAYS" ]; then
            say "this run has been going for 7 days; stopping. Use Start New Run for the next run."
            python cli.py halt >/dev/null; return 0
        fi
        clean_tree
        sync_costar

        if [ -n "$retry" ] && [ "$(git rev-parse HEAD)" = "$retry" ]; then
            # The last attempt hit an infrastructure problem: evaluate the same
            # commit again instead of asking the agent for a new one.
            pre=$(git rev-parse HEAD~1)
            subject=$(git log -1 --format=%s)
        else
            retry=""
            recover_unkept
            pre=$(git rev-parse HEAD)
            tsv_before=$(tsv_hash)
            run_agent; arc=$?
            if [ "$(tsv_hash)" != "$tsv_before" ]; then
                say "experiment_log.tsv changed during the agent round; halting. It is append-only and only the runner writes it."
                git reset -q --hard "$pre"; python cli.py halt >/dev/null; return 1
            fi
            if [ -n "$(git status --porcelain)" ]; then
                say "agent left uncommitted changes; discarding them"
                clean_tree
            fi
            n=$(git rev-list --count "$pre"..HEAD 2>/dev/null || echo 0)
            if [ "$n" -eq 0 ]; then
                if [ "$arc" -ne 0 ]; then
                    agent_fail=$((agent_fail + 1))
                    say "Claude Code failed (exit $arc, $agent_fail in a row)"
                    if [ "$agent_fail" -ge 5 ]; then
                        say "Claude Code failed 5 rounds in a row; halting. Check that it is signed in (run 'claude' in Ubuntu), then Start again."
                        python cli.py halt >/dev/null; return 1
                    fi
                else
                    noop=$((noop + 1))
                    say "agent made no experiment commit ($noop in a row)"
                fi
                sleep $(( noop + agent_fail >= 3 ? 1800 : 60 ))
                continue
            fi
            noop=0; agent_fail=0
            files=$(git diff --name-only "$pre" HEAD)
            subject=$(git log -1 --format=%s)
            if [ "$n" -ne 1 ] || [ "$(git rev-parse HEAD~1)" != "$pre" ] \
                || [ "$files" != "research.py" ] || [ "${subject#exp:}" = "$subject" ]; then
                say "rejected: an experiment is ONE new 'exp:' commit touching only research.py (got $n commit(s): $(echo $files))"
                git reset -q --hard "$pre"
                continue
            fi
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

        # A decision row for this commit wins over the exit code: the runner
        # can still fail after appending it (mirror write, purge retry).
        if [ -z "$status" ] || [ "$status" = halt ] || [ "$commit" != "$(head_short)" ]; then
            if [ -f .halt ]; then
                git reset -q --hard "$pre"
                say "halt requested; autopilot stopped"; return 0
            fi
            [ -n "$infra_since" ] || infra_since=$(date +%s)
            retry=$(git rev-parse HEAD)
            say "experiment did not run (exit $rc, row '${status:-none}'): database, harness or setup problem; retrying in 10 minutes"
            if [ $(( $(date +%s) - infra_since )) -gt 3600 ]; then
                git reset -q --hard "$pre"
                say "infrastructure failing for over an hour; halting. Check Status, then Start again."
                python cli.py halt >/dev/null; return 1
            fi
            sleep 600
            continue
        fi
        infra_since=""; retry=""
        [ "$rc" -eq 0 ] || say "note: runner exited $rc after writing its row; using the row"

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
        [ -f .halt ] && echo "Stop was requested (Start clears it)."
        echo "Run: autoresearch/$(run_tag || echo '(none yet)')"
        echo
        python cli.py log --tail 15 2>/dev/null || echo "(no experiments yet)"
        echo
        echo "Recent autopilot activity:"
        tail -n 15 "$LOG" 2>/dev/null ;;
    *)
        echo "usage: $0 {configure <settings>|start <settings> [--new-run|--autostart]|loop|stop|status}" >&2
        exit 64 ;;
esac
