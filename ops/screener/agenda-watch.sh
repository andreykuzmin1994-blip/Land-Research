#!/usr/bin/env bash
# Daily agenda watch for the screener's server (agenda_watch.py says what
# it looks for). Run by screener-agenda.timer; safe to run by hand.
#
# Three steps, kept apart:
#   1. this (run) user writes the job: the watch list and what each source
#      has already seen;
#   2. AGENDA_USER, an account that owns nothing, makes every request and
#      reads every PDF, in a folder it can read but not change, and leaves
#      one result file. Agenda packets hold documents that applicants and
#      the public filed, so this is not done as the user who owns the
#      checkout;
#   3. this user checks that file against the job, updates the state and
#      writes agenda-watch.md.
#
# It never opens the supervisor's lock (holding it would cancel a weekly
# screener run without a word): it skips the day when a screener run is
# active. Alerts carry fixed words and counts only, and are sent only when
# there is something to say. Nothing here changes a score, grade, rank,
# screen, setting or decision. See ops/screener/README.md, "Agenda watch".

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# The supervisor's settings and helpers: log, notify, and the sandbox
# helpers of investigate.sh. Sourcing runs nothing of a screener run.
# shellcheck source=supervise.sh
. "$HERE/supervise.sh"

AGENDA_SOURCES="${AGENDA_SOURCES:-$HOME/.config/screener-agenda-sources.json}"
AGENDA_USER="${AGENDA_USER-screener-agenda}"        # empty: run as this user (tests only)
AGENDA_DIR="${AGENDA_DIR:-/srv/screener-agenda}"
AGENDA_STATE="${AGENDA_STATE:-$STATE_DIR/agenda}"
AGENDA_TIMEOUT="${AGENDA_TIMEOUT:-40m}"
AGENDA_SAME_USER_OK="${AGENDA_SAME_USER_OK:-0}"     # 1 only in the offline tests
AGENDA_AS=()
[ -n "$AGENDA_USER" ] && AGENDA_AS=(sudo -n -u "$AGENDA_USER" -H --)
MAIN_LOG="$LOG_DIR/agenda-$STAMP.log"

agenda_ready() {  # prints why the watch can't run, if it can't
  [ -f "$HERE/agenda_watch.py" ] && [ -f "$HERE/changes.py" ] && [ -f "$HERE/lead_queue.py" ] &&
    [ -f "$REPO_DIR/screener/quotecheck.py" ] || { echo "a file of the agenda watch is missing from the checkout"; return 1; }
  command -v pdftotext >/dev/null 2>&1 || { echo "pdftotext is not installed (apt install poppler-utils)"; return 1; }
  [ -d "$AGENDA_DIR" ] && [ -w "$AGENDA_DIR" ] || { echo "$AGENDA_DIR missing or not writable (run install.sh --agenda-watch)"; return 1; }
  if [ -z "$AGENDA_USER" ]; then
    [ "$AGENDA_SAME_USER_OK" = 1 ] || { echo "AGENDA_USER must name the agenda-watch account"; return 1; }
  else
    local id me other name
    me="$(id -u)"
    id="$(id -u "$AGENDA_USER" 2>/dev/null)" || { echo "'$AGENDA_USER' is not an account here (run install.sh --agenda-watch)"; return 1; }
    [ "$id" != 0 ] && [ "$id" != "$me" ] || { echo "the agenda-watch account must not be this user or root"; return 1; }
    # Not an account whose processes another part force-stops, or that runs agents.
    for name in "${FIX_USER:-}" "${LEAD_USER:-}" "${CHECK_USER:-}"; do
      other="$(id -u "$name" 2>/dev/null)" || continue
      [ "$id" != "$other" ] || { echo "the agenda-watch account must be its own, not '$name'"; return 1; }
    done
    (cd / && timeout -k 5 20 "${AGENDA_AS[@]}" "$SYS_PYTHON" -I -c 'import sys') 2>/dev/null ||
      { echo "cannot run $SYS_PYTHON as '$AGENDA_USER' (run install.sh --agenda-watch)"; return 1; }
  fi
}

# One alert when a streak of a problem begins, and again each week it
# lasts: not one a day, and not once and then silence for good.
agenda_problem() {  # KEY TITLE TEXT
  local mark="$AGENDA_STATE/problem-$1"
  log "agenda watch: $3"
  if [ -e "$mark" ] && [ -z "$(find "$mark" -mtime +6 2>/dev/null)" ]; then return 0; fi
  date -u +%FT%TZ >"$mark"
  notify "$2" "$3 The watch tries again every day; this is said again each week it lasts." high
}
agenda_fine() { [ -e "$AGENDA_STATE/problem-$1" ] && unlink "$AGENDA_STATE/problem-$1"; return 0; }

agenda_main() {
  umask 027      # before the first line is logged: the log is not for everyone
  mkdir -p "$AGENDA_STATE" || exit 0
  exec 8>"$AGENDA_STATE/wrapper.lock"
  if ! flock -n 8; then log "agenda watch: another one is running; exiting"; exit 0; fi
  # A oneshot service is "activating" for as long as its run lasts (and
  # is-active says no to that), so ask for the state itself.
  if command -v systemctl >/dev/null 2>&1; then
    case "$(systemctl show -p ActiveState --value screener-supervise.service 2>/dev/null)" in
      active|activating|deactivating|reloading) log "agenda watch: a screener run is active; skipped today"; exit 0 ;;
    esac
  fi
  if [ ! -f "$AGENDA_SOURCES" ]; then
    log "agenda watch: no sources file at $AGENDA_SOURCES; nothing to watch"; exit 0
  fi
  find "$LOG_DIR" -type f -name 'agenda-*.log' -mtime +"$LOG_KEEP_DAYS" -delete 2>/dev/null
  # A command run as the sandbox account inherits this shell's working
  # folder and umask: neither the private checkout nor a loose mask.
  cd / || exit 0
  umask 027
  local why
  if ! why="$(agenda_ready)"; then
    agenda_problem setup "Screener: agenda watch not run" "Not set up: $why."; exit 0
  fi
  agenda_fine setup

  local job="$AGENDA_STATE/job.json" scan_job="$AGENDA_STATE/scan-job.json" result="$AGENDA_STATE/result.json"
  local decisions=()
  [ -f "$REPO_DIR/screener/inputs/decisions.csv" ] && decisions=(--decisions "$REPO_DIR/screener/inputs/decisions.csv")
  if ! "$PYTHON" "$HERE/agenda_watch.py" prepare --sources "$AGENDA_SOURCES" \
        --output-root "$REPO_DIR/screener/output" "${decisions[@]}" --state "$AGENDA_STATE" \
        --job "$job" --scan-job "$scan_job" >>"$MAIN_LOG" 2>&1; then
    agenda_problem prepare "Screener: agenda watch not run" \
      "The sources file or the watch's own state was refused; the reason is in the agenda log on the server."
    exit 0
  fi
  agenda_fine prepare

  local copy rc
  sandbox_empty "$AGENDA_DIR" "${AGENDA_AS[@]}" ||
    { agenda_problem sandbox "Screener: agenda watch not run" "Its handover folder could not be emptied."; exit 0; }
  copy="$(sandbox_copy "$AGENDA_DIR" scan out)" &&
    sandbox_add "$copy" agenda_watch.py "$HERE/agenda_watch.py" &&
    sandbox_add "$copy" quotecheck.py "$REPO_DIR/screener/quotecheck.py" &&
    sandbox_add "$copy" job.json "$scan_job" &&
    sandbox_seal "$copy" out ||
    { sandbox_remove "${copy:-}" "${AGENDA_AS[@]}"
      agenda_problem sandbox "Screener: agenda watch not run" "Its handover folder could not be prepared."; exit 0; }
  trap 'kill_user "$AGENDA_USER"; sandbox_remove "$copy" "${AGENDA_AS[@]}"' EXIT
  # What the scan account prints is cut at 1 MB: it must not fill the disk
  # or flood the log the alerts point to.
  (cd "$copy" && timeout -k 1m "$AGENDA_TIMEOUT" "${AGENDA_AS[@]}" "$SYS_PYTHON" -I "$copy/agenda_watch.py" \
      scan "$copy/job.json" "$copy/out/result.json" </dev/null) 2>&1 | head -c 1000000 >>"$MAIN_LOG"
  rc="${PIPESTATUS[0]}"
  log "agenda watch: scan exit $rc"
  local took=1
  if kill_user "$AGENDA_USER"; then
    [ -e "$result" ] && unlink "$result"
    "$PYTHON" "$HERE/lead_queue.py" take "$copy/out" result.json "$result" --max-bytes 4000000 >>"$MAIN_LOG" 2>&1
    took=$?
  else
    log "agenda watch: scan processes still running; its result is not read"
  fi
  sweep_tmp "$AGENDA_USER"
  sandbox_remove "$copy" "${AGENDA_AS[@]}" || log "agenda watch: could not remove $copy"
  trap - EXIT
  if [ "$rc" -ne 0 ] || [ "$took" -ne 0 ]; then
    agenda_problem scan "Screener: agenda watch did not finish" \
      "The scan stopped early or left no usable result (exit $rc). Nothing was recorded as seen."
    exit 0
  fi
  agenda_fine scan
  if ! "$PYTHON" "$HERE/agenda_watch.py" report --job "$job" --result "$result" --state "$AGENDA_STATE" >>"$MAIN_LOG" 2>&1; then
    agenda_problem report "Screener: agenda watch did not finish" \
      "The result could not be reported; the reason is in the agenda log on the server. Nothing was recorded as seen."
    exit 0
  fi
  agenda_fine report

  # The alert: counts from summary.json, which holds whole numbers only.
  local n
  n="$("$PYTHON" -c 'import json,sys
c = json.load(open(sys.argv[1]))
print(*(min(max(int(c.get(k, 0)), 0), 999999) for k in (
    "read", "parcel_hits", "street_hits", "zoning_hits", "partly", "not_read", "gave_up", "scans", "failing",
    "recovered", "stale", "storms", "no_watch_list", "sources")))' \
        "$AGENDA_STATE/summary.json" 2>>"$MAIN_LOG")" || n=""
  local read_n parcel street zoning partly not_read gave_up scans failing recovered stale storms nowatch sources
  read -r read_n parcel street zoning partly not_read gave_up scans failing recovered stale storms nowatch sources <<<"$n"
  [[ "$n" =~ ^[0-9]+(\ [0-9]+){13}$ ]] || { log "agenda watch: no counts to report"; exit 0; }
  log "agenda watch: $read_n read, $parcel parcel hits, $street street titles, $zoning zoning, $((partly + not_read + gave_up + scans)) unread, $failing failing"
  local text="" prio=default
  if [ "$parcel" -gt 0 ]; then
    text="$parcel new agenda item(s) name a parcel you watch, by its id or its address."; prio=high
  fi
  [ "$street" -gt 0 ] && text="$text $street new title(s) name a street your parcels are on (most will be about another lot)."
  [ "$zoning" -gt 0 ] && text="$text $zoning new zoning case(s) or document(s) with industrial zoning words."
  [ $((gave_up + scans + partly)) -gt 0 ] && [ -n "$text" ] &&
    text="$text $((gave_up + scans + partly)) new document(s) could not be searched in full (scans or unreadable)."
  [ "$gave_up" -gt 0 ] && [ -z "$text" ] && text="$gave_up document(s) could not be read on three runs: open them yourself."
  if [ "$failing" -gt 0 ]; then
    text="$text $failing of $sources source(s) have not answered for days: no hit from them means nothing."; prio=high
  fi
  [ "$recovered" -gt 0 ] && text="$text $recovered source(s) answer again."
  [ "$stale" -gt 0 ] && text="$text $stale source(s) have posted nothing new for longer than expected: is the index page still the right one?"
  [ "$storms" -gt 0 ] && text="$text $storms source(s) suddenly listed many documents as new; they were noted, not read: open the index page yourself."
  [ "$nowatch" -gt 0 ] && [ -n "$text" ] && text="$text There is no watch list: no finished full run was found."
  if [ -n "$text" ]; then
    notify "Screener: agenda watch" "${text# } Read agenda-watch.md in the agenda folder on the server; a hit says the words are in a document, not what was decided." "$prio"
  fi
  exit 0
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then agenda_main "$@"; fi
