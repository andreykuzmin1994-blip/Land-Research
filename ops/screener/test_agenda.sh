#!/usr/bin/env bash
# Offline tests for agenda-watch.sh: the real prepare and report steps of
# agenda_watch.py, a stand-in for the scan (which is the only step that
# would use the network; test_agenda_watch.py tests it against a made-up
# site), a throwaway checkout, no network, no sleeping.
#   Run: ops/screener/test_agenda.sh
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WATCH="$HERE/agenda-watch.sh"
ROOT="$(mktemp -d)"
[ -n "${KEEP:-}" ] || trap 'rm -rf "$ROOT"' EXIT; echo "tmp: $ROOT"
PASS=0 FAIL=0
REAL_PY="$(command -v python3)"
OWNER="ZEBULON QUARTERMAINE HOLDINGS LLC"

ok()   { PASS=$((PASS + 1)); echo "  ok   $1"; }
bad()  { FAIL=$((FAIL + 1)); echo "  FAIL $1"; }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

# The scan's stand-in: what the sandbox account would have left behind.
cat >"$ROOT/fakescan.py" <<'FAKE'
import json, os, sys
job, out, mode = json.load(open(sys.argv[1])), sys.argv[2], sys.argv[3]
src = job["sources"][0]
doc = "https://www.city.example/AgendaCenter/ViewFile/Agenda/_11172026-9"
hit = {"kind": "parcel id", "parcel_id": "09F070000320927", "page": 4,
       "text": "rezone parcel 09F070000320927 owned by ZEBULON QUARTERMAINE HOLDINGS LLC | [x](http://evil.example)"}
def result(docs, **kw):
    return {"schema": 1, "requests": 3, "bytes": 1000, "sources": {src["name"]: {
        "problem": "", "links": 5, "storm": False, "noted": [], "docs": docs, "left": 0, "titles": [], **kw}}}
def read(url=doc, hits=()):
    return {"url": url, "title": "Z26-030 Spence Road", "state": "read", "why": "", "sha256": "a" * 64,
            "pages": 9, "text_pages": 9, "hits": list(hits)}
if mode == "crash":
    print("Traceback: boom"); sys.exit(1)
body = {
    "hit": result([read(hits=[hit])]),
    "quiet": result([]),
    "offhost": result([read(url="https://evil.example/AgendaCenter/ViewFile/Agenda/_1", hits=[hit])]),
    "stranger": result([read(hits=[{**hit, "parcel_id": "NOT ON THE LIST"}])]),
    "down": result([], problem="index page not read: HTTP 503"),
    "scan": result([{**read(), "state": "not read", "why": "no text in it (a scan without text)", "text_pages": 0}]),
    "junk": "not a result",
}.get(mode)
if mode == "link":
    os.symlink("/etc/hostname", out)
elif mode == "big":
    open(out, "w").write("x" * 3_000_000)
elif mode != "none":
    json.dump(body, open(out, "w"))
FAKE
cat >"$ROOT/syspy" <<FAKE
#!/usr/bin/env bash
case "\$*" in *"agenda_watch.py scan "*)
  echo "\$PWD :: \$*" >>"\$FAKE_STATE/scan-args"; umask >"\$FAKE_STATE/scan-umask"
  find . -type f | sort | tr '\n' ' ' >"\$FAKE_STATE/scan-files"
  stat -c '%a %n' . agenda_watch.py quotecheck.py job.json out | tr '\n' ' ' >"\$FAKE_STATE/scan-modes"
  cp job.json "\$FAKE_STATE/scan-job.json"
  ls -A "\$AGENDA_DIR" | wc -l >"\$FAKE_STATE/scan-copies"
  exec "$REAL_PY" "$ROOT/fakescan.py" "\$4" "\$5" "\${FAKE_SCAN:-hit}" ;;
esac
exec "$REAL_PY" "\$@"
FAKE
mkdir -p "$ROOT/bin"
printf '#!/bin/sh\nexit 1\n' >"$ROOT/bin/pdftotext"
chmod +x "$ROOT/syspy" "$ROOT/bin/pdftotext"
export PATH="$ROOT/bin:$PATH"

# new_case NAME: a checkout with one finished run, a sources file, the config.
new_case() {
  local d="$ROOT/$1"
  mkdir -p "$d/repo/screener/output/fulton-2026-11-15" "$d/repo/screener/inputs" "$d/state" "$d/fake" "$d/agendadir" "$d/config"
  cp "$HERE/../../screener/quotecheck.py" "$d/repo/screener/"
  { echo "rank,score,parcel_id,jurisdiction,site_address,acres,lead_grade,owner,owner_mailing,decision"
    echo "1,90,09F070000320927,Union City,3600 NATURALLY FRESH BLVD,10,A,$OWNER,77 SECRET MAILING LN,"
    echo "2,89,14 0064  LL0047,Union City,0 SPENCE RD,11,B,$OWNER,77 SECRET MAILING LN,"
  } >"$d/repo/screener/output/fulton-2026-11-15/ranked.csv"
  echo '{"meta": {"county": "Fulton", "run_time": "2026-11-15T12:00:00+0000", "limit": null}}' \
    >"$d/repo/screener/output/fulton-2026-11-15/features.json"
  printf '{"sources": [{"name": "City council", "index_url": "https://www.city.example/AgendaCenter", "path_prefixes": ["/AgendaCenter/ViewFile/Agenda/"]}]}\n' \
    >"$d/config/sources.json"
  chmod 600 "$d/config/sources.json"
  export SCREENER_OPS_CONFIG=/nonexistent REPO_DIR="$d/repo" STATE_DIR="$d/state" PYTHON="$REAL_PY" \
         FAKE_STATE="$d/fake" NTFY_URL="" SLEEP=true FIX_USER="" LEAD_USER="" CHECK_USER="" \
         AGENDA_SOURCES="$d/config/sources.json" AGENDA_USER="" AGENDA_SAME_USER_OK=1 AGENDA_DIR="$d/agendadir" \
         SYS_PYTHON="$ROOT/syspy"
  unset FAKE_SCAN AGENDA_STATE
  CASE="$d"
}
alerts() { cat "$CASE/state/logs/alerts.log" 2>/dev/null; }
md() { cat "$CASE/state/agenda/agenda-watch.md" 2>/dev/null; }
seen() { grep -c '"state": "read"' "$CASE/state/agenda/state.json" 2>/dev/null; }

echo "a new document names a watched parcel"
new_case hit; "$WATCH" 2>/dev/null; rc=$?
check "exit 0" '[ $rc -eq 0 ]'
check "the report names the parcel and the document" 'md | grep -q "09F070000320927" && md | grep -q "_11172026-9"'
check "the tax-roll owner is withheld and the text is inert" '! md | grep -q -e QUARTERMAINE -e "](" -e "evil.example"'
check "the state remembers the document" '[ "$(seen)" = 1 ]'
check "one alert, with counts" '[ "$(alerts | grep -c "Screener: agenda watch")" = 1 ] && alerts | grep -q "1 new agenda item(s) name a parcel you watch or its street"'
check "the alert is high priority" 'grep -q "\[high\] Screener: agenda watch" $CASE/state/logs/alerts.log'
check "the alert names no parcel, street, owner or source" '! alerts | grep -q -e 09F07 -e Spence -e QUARTERMAINE -e "City council" -e city.example'
check "the scan ran isolated, from its own folder" 'grep -q "^$CASE/agendadir/scan\.[A-Za-z0-9]* :: -I $CASE/agendadir/scan\.[A-Za-z0-9]*/agenda_watch.py scan $CASE/agendadir/scan\.[A-Za-z0-9]*/job.json $CASE/agendadir/scan\.[A-Za-z0-9]*/out/result.json$" $CASE/fake/scan-args'
check "with a closed umask" '[ "$(cat $CASE/fake/scan-umask)" = 0027 ]'
check "it has the tool, the fetcher and the job, nothing else" '[ "$(cat $CASE/fake/scan-files)" = "./agenda_watch.py ./job.json ./quotecheck.py " ]'
check "only its out folder is writable" '[ "$(cat $CASE/fake/scan-modes | sed "s/ [^ ]* / /g; s/ [^ ]*$//")" = "2750 640 640 640 2770" ]'
check "its job holds search keys only" '! grep -q -e QUARTERMAINE -e "SECRET MAILING" -e grade -e rank -e "$CASE/repo" $CASE/fake/scan-job.json && grep -q "09F070000320927" $CASE/fake/scan-job.json'
check "one sandbox folder at a time, none afterwards" '[ "$(cat $CASE/fake/scan-copies)" = 1 ] && [ -z "$(ls -A $CASE/agendadir)" ]'
check "the supervisor's lock was never opened" '[ ! -e $CASE/state/supervise.lock ]'
check "nothing was written into the checkout" '[ -z "$(find $CASE/repo -newer $CASE/config/sources.json -type f)" ]'

echo "a day with nothing new sends no alert"
new_case quiet; FAKE_SCAN=quiet "$WATCH" 2>/dev/null; rc=$?
check "exit 0" '[ $rc -eq 0 ]'
check "no alert" '[ -z "$(alerts)" ]'
check "the report is still written" 'md | grep -q "No hit does not mean no item"'

echo "no sources file: nothing to do, nothing said"
new_case nosources; unlink "$CASE/config/sources.json"; "$WATCH" 2>/dev/null; rc=$?
check "exit 0, no alert, no scan" '[ $rc -eq 0 ] && [ -z "$(alerts)" ] && [ ! -f $CASE/fake/scan-args ]'

echo "not set up: said once, not every day"
new_case unready; AGENDA_DIR="$CASE/nowhere" "$WATCH" 2>/dev/null; AGENDA_DIR="$CASE/nowhere" "$WATCH" 2>/dev/null
check "one alert that says why" '[ "$(alerts | grep -c "agenda watch not run")" = 1 ] && alerts | grep -q "install.sh --agenda-watch"'
check "no scan" '[ ! -f $CASE/fake/scan-args ]'
"$WATCH" 2>/dev/null
check "once it is set up the watch runs" '[ -f $CASE/fake/scan-args ]'
new_case noaccount; AGENDA_SAME_USER_OK=0 "$WATCH" 2>/dev/null
check "an empty account name is refused outside the tests" 'alerts | grep -q "must name the agenda-watch account" && [ ! -f $CASE/fake/scan-args ]'
new_case selfaccount; AGENDA_USER="$(id -un)" "$WATCH" 2>/dev/null
check "this user or root as the account is refused" 'alerts | grep -q "must not be this user or root" && [ ! -f $CASE/fake/scan-args ]'

echo "a sources file that others can write is refused"
new_case loose; chmod 666 "$CASE/config/sources.json"; "$WATCH" 2>/dev/null
check "alert, no scan" 'alerts | grep -q "agenda watch not run" && [ ! -f $CASE/fake/scan-args ]'

for kind in crash none link big junk; do
  echo "the scan leaves nothing usable ($kind)"
  new_case "bad-$kind"; FAKE_SCAN=$kind "$WATCH" 2>/dev/null; rc=$?
  check "exit 0" '[ $rc -eq 0 ]'
  check "said once" '[ "$(alerts | grep -c "agenda watch did not finish")" = 1 ]'
  check "nothing recorded as seen, no report" '[ ! -f $CASE/state/agenda/state.json ] && [ -z "$(md)" ]'
  check "sandbox folder empty" '[ -z "$(ls -A $CASE/agendadir)" ]'
  FAKE_SCAN=$kind "$WATCH" 2>/dev/null
  check "not said again the next day" '[ "$(alerts | grep -c "agenda watch did not finish")" = 1 ]'
  FAKE_SCAN=quiet "$WATCH" 2>/dev/null; FAKE_SCAN=$kind "$WATCH" 2>/dev/null
  check "said again after it had worked in between" '[ "$(alerts | grep -c "agenda watch did not finish")" = 2 ]'
done

for kind in offhost stranger; do
  echo "a result that does not fit the job is not believed ($kind)"
  new_case "odd-$kind"; FAKE_SCAN=$kind "$WATCH" 2>/dev/null
  check "no parcel hit in the report or the alert" '! md | grep -q "| parcel id |" && ! alerts | grep -q "name a parcel"'
done

echo "a source that is down is said on its third day"
new_case down
for day in 1 2 3 4; do
  FAKE_SCAN=down "$WATCH" 2>/dev/null
  # one strike a day: move the recorded day back so the next run is "the next day"
  "$REAL_PY" - "$CASE/state/agenda/state.json" "$day" <<'PY'
import json, sys
p = sys.argv[1]; s = json.load(open(p))
for src in s["sources"].values(): src["fail_day"] = f"2026-01-0{sys.argv[2]}"
json.dump(s, open(p, "w"))
PY
done
check "one alert, on the third day" '[ "$(alerts | grep -c "have not answered for days")" = 1 ]'
check "the report says no hit from it means nothing" 'md | grep -q "No hit from it means nothing"'

echo "a scanned document is counted, not passed over"
new_case scanned; FAKE_SCAN=scan "$WATCH" 2>/dev/null
check "the report lists it" 'md | grep -q "1 documents are scans without text"'
check "no alert for a scan alone" '[ -z "$(alerts)" ]'

echo "while a screener run is active the watch skips the day"
new_case busy; printf '#!/bin/sh\n[ "$1" = is-active ] && exit 0\nexit 1\n' >"$ROOT/bin/systemctl"; chmod +x "$ROOT/bin/systemctl"
"$WATCH" 2>/dev/null; rc=$?
check "exit 0, no scan, no alert" '[ $rc -eq 0 ] && [ ! -f $CASE/fake/scan-args ] && [ -z "$(alerts)" ]'
unlink "$ROOT/bin/systemctl"

echo "a second watch does not run beside the first"
new_case twice; mkdir -p "$CASE/state/agenda"
( exec 8>"$CASE/state/agenda/wrapper.lock"; flock -n 8; "$WATCH" 2>/dev/null )
check "no scan" '[ ! -f $CASE/fake/scan-args ]'

echo "a loose umask of the caller does not open the sandbox"
new_case umask; (umask 002; "$WATCH" 2>/dev/null)
check "only its out folder is writable" '[ "$(cat $CASE/fake/scan-modes | sed "s/ [^ ]* / /g; s/ [^ ]*$//")" = "2750 640 640 640 2770" ]'

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
