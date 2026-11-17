#!/usr/bin/env bash
# Offline tests for the lead checks (investigate.sh, lead_queue.py) and the
# run-to-run digest as supervise.sh runs them: a fake screener, fake agents,
# the REAL quote checker on findings without claims (so nothing is
# fetched), a throwaway git repository, no network, no sleeping.
#   Run: ops/screener/test_investigate.sh
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUP="$HERE/supervise.sh"
ROOT="$(mktemp -d)"
[ -n "${KEEP:-}" ] || trap 'rm -rf "$ROOT"' EXIT; echo "tmp: $ROOT"
PASS=0 FAIL=0
REAL_PY="$(command -v python3)"
OWNER="ZEBULON QUARTERMAINE HOLDINGS LLC"

ok()   { PASS=$((PASS + 1)); echo "  ok   $1"; }
bad()  { FAIL=$((FAIL + 1)); echo "  FAIL $1"; }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

# Fake python: screener.run writes a small run folder; anything else is the
# real interpreter (changes.py, lead_queue.py).
cat >"$ROOT/fakepy" <<FAKE
#!/usr/bin/env bash
case "\${2:-}" in
  pip) exit 0 ;;
  screener.run)
    [ "\${FAKE_RUN:-ok}" = fail ] && { echo "  ! the county parcel layer is unavailable (HTTP 404)."; exit 1; }
    [ "\${FAKE_RUN:-ok}" = nowrite ] && exit 0      # finished, but wrote no new folder
    n=\$(( \$(cat "\$FAKE_STATE/count" 2>/dev/null || echo 0) + 1 )); echo "\$n" >"\$FAKE_STATE/count"
    out="screener/output/fake-2026-11-\$(printf %02d \$((10 + n)))"
    mkdir -p "\$out"
    {
      echo "rank,score,parcel_id,jurisdiction,site_address,acres,zoning,entitlement,lead_grade,lead_found,lead_not_checked,owner,owner_mailing,last_sale_date,decision,maps_link"
      echo "1,90,14F 1,Union City,1 TEST RD,10,M-1,by_right,A,,,$OWNER,77 SECRET MAILING LN,2001-01-01,,https://www.google.com/maps/search/?api=1&query=33.6,-84.5"
      echo "2,89,A2,Union City,2 TEST RD,11,M-1,by_right,A,,,$OWNER,77 SECRET MAILING LN,2001-01-01,,"
      echo "3,88,B 3,Union City,3 TEST RD,12,M-1,by_right,B,no pond room,,$OWNER,77 SECRET MAILING LN,2001-01-01,,"
      echo "4,87,C4,Union City,4 TEST RD,13,M-1,by_right,C,a; b,,$OWNER,77 SECRET MAILING LN,2001-01-01,,"
      echo "5,86,../evil,Union City,5 TEST RD,13,M-1,by_right,A,,,$OWNER,77 SECRET MAILING LN,2001-01-01,,"
    } >"\$out/ranked.csv"
    echo "parcel_id,jurisdiction,site_address,acres,owner,decision,reasons" >"\$out/rejected.csv"
    if [ "\${FAKE_RUN:-ok}" = warn ]; then echo "- SOIL SERVICE FAILURES: re-run later" >"\$out/summary.md"
    else echo "# done" >"\$out/summary.md"; fi
    exit 0 ;;
esac
exec "$REAL_PY" "\$@"
FAKE

# The "system Python" of the tests: the real one, with its arguments noted.
cat >"$ROOT/syspy" <<FAKE
#!/usr/bin/env bash
echo "\$PWD :: \$*" >>"\$FAKE_STATE/syspy-args"
exec "$REAL_PY" "\$@"
FAKE

# Fake agent: acts in its working directory (the sandbox copy) per
# $FAKE_LEAD (investigator) and $FAKE_SKEPTIC (skeptic).
cat >"$ROOT/fakeclaude" <<'FAKE'
#!/usr/bin/env bash
[ "$1" = --version ] && exit 0
role=investigator; [[ "$*" == *"You are the skeptic"* ]] && role=skeptic
echo "$*" >"$FAKE_STATE/args-$role"
echo "${LEAD_FETCH_MAX:-unset}" >"$FAKE_STATE/fetchmax-$role"
pwd >"$FAKE_STATE/cwd-$role"; umask >"$FAKE_STATE/umask-$role"
leaf="$(ls -d screener/leads/*/ | head -1)"; leaf="${leaf%/}"
card=screener/lead-card/card.md
pid="$(sed -n 's/^# Lead card: parcel \(.*\), run .*/\1/p' "$card")"
run="$(sed -n 's/^# Lead card: parcel .*, run \(.*\)/\1/p' "$card")"
echo "$pid" >>"$FAKE_STATE/pids-$role"
find . -type f | sort >"$FAKE_STATE/files-$role"
grep -rIl -e "QUARTERMAINE" -e "SECRET MAILING" . >"$FAKE_STATE/owner-seen-$role" 2>/dev/null
stat -c '%a %n' . screener tools tools/lead_fetch.py "$card" "$leaf" >"$FAKE_STATE/modes-$role"
ls -A "$LEAD_DIR" | wc -l >"$FAKE_STATE/copies-$role"
[ -e .git ] && touch "$FAKE_STATE/saw-git"
sections='"sections":{"zoning":{"verdict":"unknown","claims":[],"open_items":["nothing found"]},"plans":{"verdict":"unknown","claims":[],"open_items":[]},"utilities":{"verdict":"unknown","claims":[],"open_items":[]},"market":{"verdict":"unknown","claims":[],"open_items":[]},"owner":{"verdict":"unknown","claims":[],"open_items":[]}}'
good() { printf '{"schema":1,"parcel_id":"%s","run":"%s",%s}' "$1" "$2" "$sections"; }
if [ "$role" = investigator ]; then
  case "${FAKE_LEAD:-ok}" in
    ok)          good "$pid" "$run" >"$leaf/findings.json" ;;
    extra)       good "$pid" "$run" >"$leaf/findings.json"; echo "my notes" >"$leaf/notes.txt"
                 mkdir -p "$leaf/pages" && echo page >"$leaf/pages/p.html" ;;
    symlink)     ln -s /etc/hostname "$leaf/findings.json" ;;
    fifo)        mkfifo "$leaf/findings.json" ;;
    hardlink)    good "$pid" "$run" >"$leaf/other.json"; ln "$leaf/other.json" "$leaf/findings.json" ;;
    big)         head -c 2000000 /dev/zero >"$leaf/findings.json" ;;
    wrongparcel) good "SOMEONE ELSE" "$run" >"$leaf/findings.json" ;;
    wrongrun)    good "$pid" "fake-1999-01-01" >"$leaf/findings.json" ;;
    notjson)     echo "{not json" >"$leaf/findings.json" ;;
    extrafield)  printf '{"schema":1,"parcel_id":"%s","run":"%s","headline":"x",%s}' "$pid" "$run" "$sections" >"$leaf/findings.json" ;;
    settings)    good "$pid" "$run" >"$leaf/findings.json"
                 mkdir -p "$LEAD_HOME/.claude" && echo '{"hooks":{}}' >"$LEAD_HOME/.claude/settings.json" ;;
    binary)      good "$pid" "$run" >"$leaf/findings.json"
                 mkdir -p "$LEAD_HOME/.local/bin" && echo '#!/bin/sh' >"$LEAD_HOME/.local/bin/claude" ;;
    zerolink)    good "$pid" "$run" >"$leaf/findings.json"
                 mkdir -p "$LEAD_HOME/.ssh" && ln -s /dev/zero "$LEAD_HOME/.ssh/k" ;;
    locked)      good "$pid" "$run" >"$leaf/findings.json"
                 mkdir -p "$leaf/shut/in" && echo x >"$leaf/shut/in/f" && chmod 000 "$leaf/shut" ;;
    crash)       echo "Invalid API key · Please run /login"; exit 1 ;;
    timeout)     exit 124 ;;
    none)        ;;
  esac
else
  [ -e "$leaf/notes.txt" ] && touch "$FAKE_STATE/skeptic-saw-notes"
  sk() { printf '{"schema":1,"parcel_id":"%s","skeptic":{"verdict":"%s","objection":"An agent reading.","claims":[],"open_items":[]}}' "$pid" "$1" >"$leaf/skeptic.json"; }
  want="${FAKE_SKEPTIC:-weakened}"
  case "$want" in
    killed|weakened|survives) sk "$want" ;;
    badverdict)  sk "definitely fine" ;;
    wrongparcel) pid="SOMEONE ELSE"; sk killed ;;
    settings)    sk killed; echo "alias x=y" >>"$LEAD_HOME/.bashrc" ;;
    none)        ;;
  esac
fi
exit 0
FAKE
chmod +x "$ROOT/fakepy" "$ROOT/fakeclaude" "$ROOT/syspy"

# new_case NAME: fresh repo + state; exports the supervisor's config.
new_case() {
  local d="$ROOT/$1"
  mkdir -p "$d/repo/screener" "$d/state" "$d/fake" "$d/leaddir" "$d/checkdir" "$d/leadhome"
  (cd "$d/repo" && git init -q -b main && git config user.email t@t && git config user.name t &&
   printf '[run]\ncounty = "fake"\n' >screener/settings.toml && : >screener/requirements.txt &&
   cp "$HERE/../../screener/quotecheck.py" "$HERE/../../screener/INVESTIGATE.md" screener/ &&
   printf 'screener/output/\nscreener/leads/\n' >.gitignore && git add -A && git commit -qm init)
  export SCREENER_OPS_CONFIG=/nonexistent REPO_DIR="$d/repo" STATE_DIR="$d/state" \
         PYTHON="$ROOT/fakepy" FAKE_STATE="$d/fake" PULL=0 SOILS_QUIET=0 SLEEP=true RETRY_WAITS="5 5" \
         NTFY_URL="" CLAUDE_FIX=0 FIX_USER="" \
         INVESTIGATE=1 LEAD_USER="" CHECK_USER="" LEAD_DIR="$d/leaddir" CHECK_DIR="$d/checkdir" \
         LEAD_HOME="$d/leadhome" LEAD_CLAUDE_BIN="$ROOT/fakeclaude" SYS_PYTHON="$ROOT/syspy" \
         INVESTIGATE_SAME_USER_OK=1 LEAD_MIN_FREE_MB=0 INVESTIGATE_MAX=5 INVESTIGATE_GRADES="A B" INVESTIGATE_MAX_TRIES=2
  unset LEADS_DIR FAKE_RUN FAKE_LEAD FAKE_SKEPTIC
  CASE="$d"
}
alerts() { cat "$CASE/state/logs/alerts.log" 2>/dev/null; }
leads() { ls -A "$CASE/repo/screener/leads" 2>/dev/null; }
lead_files() { ls -A "$CASE/repo/screener/leads/$1" 2>/dev/null | sort | tr '\n' ' '; }
run_dir() { ls -1d "$CASE"/repo/screener/output/fake-* | tail -1; }
leftovers() { ls -A "$CASE/leaddir" "$CASE/checkdir" 2>/dev/null | grep -v ':$' | grep -c .; }

echo "three new leads are checked and published"
new_case happy; FAKE_SKEPTIC=killed "$SUP" 2>/dev/null; rc=$?
check "exit 0" '[ $rc -eq 0 ]'
check "A leads first, then B; the unsafe id is never handed over" '[ "$(tr "\n" "," <$CASE/fake/pids-investigator)" = "14F 1,A2,B 3," ]'
check "three lead folders" '[ "$(leads | sort | tr "\n" " ")" = "14F_1 A2 B_3 " ]'
check "each holds the four files and nothing else" '[ "$(lead_files 14F_1)" = "findings.checked.json findings.json lead.md skeptic.json " ]'
check "the checked file carries the skeptic verdict" 'grep -q "\"verdict\": \"killed\"" $CASE/repo/screener/leads/14F_1/findings.checked.json'
check "alert has the counts" 'alerts | grep -q "3 lead(s) checked against public documents: 3 where the skeptic found a document that kills the lead"'
check "alert names no parcel, address or owner" '! alerts | grep -A3 "lead checks" | grep -q -e "14F" -e "TEST RD" -e "QUARTERMAINE" -e "A2"'
check "the agents ran in dontAsk mode without session persistence" 'grep -q -- "--permission-mode dontAsk --no-session-persistence" $CASE/fake/args-investigator'
tools() { sed -n 's/.*--allowedTools \(.*\) --disallowedTools \(.*\)$/\1 || \2/p' "$CASE/fake/args-$1" | sed "s#$CASE/leaddir/[a-z]*\.[A-Za-z0-9]*#COPY#"; }
check "the investigator's tools are exactly these" '[ "$(tools investigator)" = "Read Glob Grep Write WebSearch Bash($ROOT/syspy -I COPY/tools/lead_fetch.py:*) || WebFetch" ]'
check "the skeptic's tools are exactly these" '[ "$(tools skeptic)" = "Read Glob Grep Write WebSearch Bash($ROOT/syspy -I COPY/tools/lead_fetch.py:*) || WebFetch" ]'
check "the quote check ran isolated, from its own copy, twice per lead" '[ "$(grep -c -- " :: -I $CASE/checkdir/check[12]\.[A-Za-z0-9]*/quotecheck.py $CASE/checkdir/check[12]\.[A-Za-z0-9]*/[A-Za-z0-9_]*/findings.json" $CASE/fake/syspy-args)" = 6 ]'
check "the second pass got the hash of the staged findings" 'grep -q -- "--with-skeptic --findings-sha256 [0-9a-f]\{64\}$" $CASE/fake/syspy-args'
check "never from the checkout's own quotecheck.py" '! grep -q "$CASE/repo/screener/quotecheck.py" $CASE/fake/syspy-args'
check "the request allowance reaches the agent" '[ "$(cat $CASE/fake/fetchmax-investigator)" = 40 ]'
check "no owner name or mailing address in the investigator's folder" '[ ! -s $CASE/fake/owner-seen-investigator ]'
check "none in the skeptic's folder" '[ ! -s $CASE/fake/owner-seen-skeptic ]'
check "the investigator has the card, the rules and the fetch tool only" '[ "$(tr "\n" " " <$CASE/fake/files-investigator)" = "./screener/INVESTIGATE.md ./screener/lead-card/card.md ./tools/lead_fetch.py ./tools/quotecheck.py " ]'
check "the skeptic also has the checked file, and not lead.md or findings.json" 'grep -q "lead-card/findings.checked.json" $CASE/fake/files-skeptic && ! grep -q -e "lead.md" -e "findings.json$" $CASE/fake/files-skeptic'
check "no git directory in the sandbox" '[ ! -f $CASE/fake/saw-git ]'
check "the agent starts in its own folder with a closed umask" 'grep -q "^$CASE/leaddir/investigator\." $CASE/fake/cwd-investigator && [ "$(cat $CASE/fake/umask-investigator)" = 0027 ]'
check "only the lead's own folder is writable" '[ "$(awk "{print \$1}" $CASE/fake/modes-investigator | tr "\n" " ")" = "2750 2750 2750 640 640 2770 " ]'
check "one sandbox folder at a time" '[ "$(cat $CASE/fake/copies-investigator)" = 1 ] && [ "$(cat $CASE/fake/copies-skeptic)" = 1 ]'
check "both sandbox folders are empty afterwards" '[ "$(leftovers)" = 0 ]'
check "no staging left behind" '[ -z "$(ls -A $CASE/state/investigate | grep -v attempts)" ] && [ -z "$(ls -A $CASE/state/investigate/attempts)" ]'
check "changes.md shows the document checks" 'grep -q "0 survives, 0 weakened, 3 killed; 1 not checked yet" $(run_dir)/changes.md && grep "14F 1" $(run_dir)/changes.md | grep -q killed'
check "changes.md has no owner name" '! grep -q -e QUARTERMAINE -e "SECRET MAILING" $(run_dir)/changes.md'
check "the summary alert carries the digest counts" 'alerts | grep -q "first run, nothing to compare. Grade A 3, grade B 1"'
check "no HOLD" '[ ! -e $CASE/state/investigate/HOLD ]'

echo "the next run checks nothing twice and reports what changed"
FAKE_SKEPTIC=killed "$SUP" 2>/dev/null
check "agents not called again" '[ "$(wc -l <$CASE/fake/pids-investigator)" = 3 ]'
check "the second run is compared with the first" 'alerts | grep -q "fake-2026-11-12 vs fake-2026-11-11: grade A 3 (0 new)"'
check "no second lead-check alert" '[ "$(alerts | grep -c "Screener: lead checks")" = 1 ]'

echo "off by default"
new_case off; unset INVESTIGATE; "$SUP" 2>/dev/null
check "agents not called" '[ ! -f $CASE/fake/args-investigator ]'
check "no lead folder" '[ -z "$(leads)" ]'
check "the digest is still written" '[ -f $(run_dir)/changes.md ]'

echo "a trial run (extra arguments) is not checked"
new_case trial; "$SUP" --limit 50 2>/dev/null
check "agents not called" '[ ! -f $CASE/fake/args-investigator ]'

echo "a run with warnings is not checked"
new_case warned; FAKE_RUN=warn "$SUP" 2>/dev/null
check "agents not called" '[ ! -f $CASE/fake/args-investigator ]'
check "warning alert" 'alerts | grep -q "done with warnings"'

echo "not set up: an alert, no agents, the run still counts as ok"
new_case unready; LEAD_DIR="$CASE/nowhere" "$SUP" 2>/dev/null; rc=$?
check "exit 0" '[ $rc -eq 0 ]'
check "alert says why" 'alerts | grep -q "lead checks not run" && alerts | grep -q "install.sh --lead-checks"'
check "agents not called" '[ ! -f $CASE/fake/args-investigator ]'
new_case sameuser; LEAD_USER="$(id -un)" CHECK_USER="$(id -un)" LEAD_HOME="" "$SUP" 2>/dev/null
check "one account for the agents and the check is refused" 'alerts | grep -q "must differ from each other, from this user"'
check "agents not called" '[ ! -f $CASE/fake/args-investigator ]'
new_case nousers; INVESTIGATE_SAME_USER_OK=0 "$SUP" 2>/dev/null
check "empty account names are refused outside the tests" 'alerts | grep -q "must name the two lead-check accounts"'
check "agents not called" '[ ! -f $CASE/fake/args-investigator ]'
new_case nowatch; LEAD_HOME="$CASE/no-such-home" "$SUP" 2>/dev/null
check "a watch that cannot look is not taken for unchanged" 'alerts | grep -q "settings cannot be read for the watch"'
check "agents not called" '[ ! -f $CASE/fake/args-investigator ]'

echo "a failed county: exit 1, and the lead checks do not change that"
new_case failed; FAKE_RUN=fail "$SUP" 2>/dev/null; rc=$?
check "exit 1" '[ $rc -eq 1 ]'
check "agents not called" '[ ! -f $CASE/fake/args-investigator ]'

echo "an output folder from before this run is never checked"
new_case stalerun; "$SUP" 2>/dev/null; rm -rf "$CASE/repo/screener/leads" "$CASE"/fake/*
touch -d "2 hours ago" "$CASE"/repo/screener/output/fake-2026-11-11/ranked.csv
FAKE_RUN=nowrite "$SUP" 2>/dev/null; rc=$?
check "exit 0" '[ $rc -eq 0 ]'
check "agents not called" '[ ! -f $CASE/fake/args-investigator ]'
check "no lead folder" '[ -z "$(leads)" ]'

echo "a list of leads that cannot be made is said, not passed over"
new_case badgrades; INVESTIGATE_GRADES="A,B" "$SUP" 2>/dev/null; rc=$?
check "exit 0" '[ $rc -eq 0 ]'
check "alert" 'alerts | grep -q "For 1 county(ies) the list of leads could not be made"'
check "agents not called" '[ ! -f $CASE/fake/args-investigator ]'

echo "a loose umask of the caller does not open the sandbox"
new_case umask; (umask 002; INVESTIGATE_MAX=1 "$SUP" 2>/dev/null)
check "only the lead's own folder is writable" '[ "$(awk "{print \$1}" $CASE/fake/modes-investigator | tr "\n" " ")" = "2750 2750 2750 640 640 2770 " ]'
check "published" '[ "$(leads)" = "14F_1" ]'

for kind in symlink fifo hardlink big wrongparcel wrongrun notjson extrafield none; do
  echo "the investigator leaves a bad findings.json ($kind)"
  new_case "bad-$kind"; INVESTIGATE_MAX=1 FAKE_LEAD=$kind "$SUP" 2>/dev/null; rc=$?
  check "exit 0" '[ $rc -eq 0 ]'
  check "nothing published" '[ -z "$(leads)" ]'
  check "the skeptic never ran" '[ ! -f $CASE/fake/args-skeptic ]'
  check "counted as not finished" 'alerts | grep -q "0 lead(s) checked" && alerts | grep -q "1 check(s) did not finish"'
  check "the try is counted" '[ "$(cat $CASE/state/investigate/attempts/14F_1)" = 1 ]'
  check "sandbox folders empty" '[ "$(leftovers)" = 0 ]'
done

echo "a lead that keeps failing is left alone after two tries"
new_case giveup; INVESTIGATE_MAX=1 FAKE_LEAD=none "$SUP" 2>/dev/null
INVESTIGATE_MAX=1 FAKE_LEAD=none "$SUP" 2>/dev/null
INVESTIGATE_MAX=1 FAKE_LEAD=ok "$SUP" 2>/dev/null
check "the third run moved on to the next lead" '[ "$(tr "\n" "," <$CASE/fake/pids-investigator)" = "14F 1,14F 1,A2," ]'
check "and published it" '[ "$(leads)" = "A2" ]'
check "the alert counts the lead that is no longer tried" 'alerts | tail -6 | grep -q "1 lead(s) are no longer tried after 2 failed tries"'

for kind in none badverdict wrongparcel; do
  echo "the skeptic leaves nothing usable ($kind): no half result"
  new_case "skeptic-$kind"; INVESTIGATE_MAX=1 FAKE_SKEPTIC=$kind "$SUP" 2>/dev/null
  check "nothing published" '[ -z "$(leads)" ]'
  check "counted as not finished" 'alerts | grep -q "1 check(s) did not finish"'
  check "sandbox folders empty" '[ "$(leftovers)" = 0 ]'
done

echo "what the investigator leaves behind never reaches the skeptic or the result"
new_case extra; INVESTIGATE_MAX=1 FAKE_LEAD=extra FAKE_SKEPTIC=survives "$SUP" 2>/dev/null
check "published" '[ "$(leads)" = "14F_1" ]'
check "the skeptic did not see the notes" '[ ! -f $CASE/fake/skeptic-saw-notes ]'
check "only the four files" '[ "$(lead_files 14F_1)" = "findings.checked.json findings.json lead.md skeptic.json " ]'
check "the wording for no objection" 'alerts | grep -q "1 where the skeptic raised no objection (an agent.s reading, not a clearance)"'

echo "a lead folder that is already there is never touched"
new_case exists; mkdir -p "$CASE/repo/screener/leads/14F_1" && echo "by hand" >"$CASE/repo/screener/leads/14F_1/lead.md"
INVESTIGATE_MAX=1 "$SUP" 2>/dev/null
check "the hand-made file is intact" '[ "$(cat $CASE/repo/screener/leads/14F_1/lead.md)" = "by hand" ]'
check "the next lead was checked instead" '[ "$(cat $CASE/fake/pids-investigator)" = "A2" ]'

echo "leftovers of an interrupted run are cleared before an agent starts"
new_case stale; mkdir -p "$CASE/leaddir/lead.old/screener" "$CASE/checkdir/check1.old" && echo x >"$CASE/leaddir/lead.old/screener/f"
INVESTIGATE_MAX=1 "$SUP" 2>/dev/null
check "published" '[ "$(leads)" = "14F_1" ]'
check "the agent saw one folder" '[ "$(cat $CASE/fake/copies-investigator)" = 1 ]'

echo "an agent that changes its own account's settings puts the checks on hold"
new_case hold; FAKE_LEAD=settings "$SUP" 2>/dev/null; rc=$?
check "exit 0" '[ $rc -eq 0 ]'
check "nothing published" '[ -z "$(leads)" ]'
check "HOLD file written" '[ -f $CASE/state/investigate/HOLD ]'
check "urgent alert" 'grep -q "\[urgent\] Screener: lead checks" $CASE/state/logs/alerts.log && alerts | grep -q "STOPPED: a check changed"'
check "only the first lead was started" '[ "$(wc -l <$CASE/fake/pids-investigator)" = 1 ]'
rm -f "$CASE/fake/args-investigator"; FAKE_LEAD=ok "$SUP" 2>/dev/null
check "the next run checks nothing" '[ ! -f $CASE/fake/args-investigator ]'
check "and says it is on hold" 'alerts | grep -q "lead checks are on hold"'
new_case hold2; INVESTIGATE_MAX=1 FAKE_SKEPTIC=settings "$SUP" 2>/dev/null
check "the skeptic doing it holds too, with nothing published" '[ -f $CASE/state/investigate/HOLD ] && [ -z "$(leads)" ]'

for kind in binary zerolink; do
  echo "the watch also sees the account's copy of Claude Code and a link in .ssh ($kind)"
  new_case "hold-$kind"; INVESTIGATE_MAX=1 FAKE_LEAD=$kind timeout 120 "$SUP" 2>/dev/null; rc=$?
  check "finished, exit 0" '[ $rc -eq 0 ]'
  check "HOLD, nothing published" '[ -f $CASE/state/investigate/HOLD ] && [ -z "$(leads)" ]'
done

echo "an agent that cannot start stops the stage and costs no try"
new_case crash; FAKE_LEAD=crash "$SUP" 2>/dev/null; rc=$?
check "exit 0" '[ $rc -eq 0 ]'
check "only the first lead was started" '[ "$(wc -l <$CASE/fake/pids-investigator)" = 1 ]'
check "urgent alert names the login" 'grep -q "\[urgent\] Screener: lead checks" $CASE/state/logs/alerts.log && alerts | grep -q "STOPPED: the agent ended with an error"'
check "no try counted" '[ -z "$(ls -A $CASE/state/investigate/attempts)" ]'
check "the agent's own words are in its log, not in the alert" 'grep -q "Invalid API key" $CASE/state/logs/lead-*.log && ! alerts | grep -q "Invalid API key"'

echo "an agent stopped at its time limit is a try of that lead"
new_case timelimit; INVESTIGATE_MAX=1 FAKE_LEAD=timeout "$SUP" 2>/dev/null
check "the try is counted" '[ "$(cat $CASE/state/investigate/attempts/14F_1)" = 1 ]'
check "not an agent error" '! alerts | grep -q "STOPPED"'

echo "a folder the agent closed to itself is still removed"
new_case locked; INVESTIGATE_MAX=1 FAKE_LEAD=locked "$SUP" 2>/dev/null
check "published" '[ "$(leads)" = "14F_1" ]'
check "sandbox folders empty" '[ "$(leftovers)" = 0 ]'

echo "a time budget of zero starts no agent"
new_case budget; INVESTIGATE_BUDGET=0 "$SUP" 2>/dev/null
check "agents not called" '[ ! -f $CASE/fake/args-investigator ]'
check "alert says leads were left" 'alerts | grep -q "3 lead(s) left for the next run. Stopped at the time limit"'

echo "too little free disk starts no agent"
new_case disk; LEAD_MIN_FREE_MB=999999999 "$SUP" 2>/dev/null
check "agents not called" '[ ! -f $CASE/fake/args-investigator ]'
check "alert says so" 'alerts | grep -q "of free disk"'

echo "only grade A when asked"
new_case gradea; INVESTIGATE_GRADES="A" "$SUP" 2>/dev/null
check "two leads" '[ "$(leads | sort | tr "\n" " ")" = "14F_1 A2 " ]'

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
