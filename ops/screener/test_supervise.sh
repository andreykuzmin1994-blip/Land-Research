#!/usr/bin/env bash
# Offline tests for supervise.sh: fake screener, fake Claude, a throwaway
# git repository, no network, no sleeping.  Run: ops/screener/test_supervise.sh
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUP="$HERE/supervise.sh"
ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT
PASS=0 FAIL=0

ok()   { PASS=$((PASS + 1)); echo "  ok   $1"; }
bad()  { FAIL=$((FAIL + 1)); echo "  FAIL $1"; }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

# Fake python: pip/unittest succeed; screener.run follows $FAKE_RUN.
#   ok | fail | failN (fail the first N runs) | refresh (fail, mentioning --refresh, until run with it)
cat >"$ROOT/fakepy" <<'EOF'
#!/usr/bin/env bash
case "$2" in
  pip|unittest) exit 0 ;;
  screener.run)
    echo "$*" >>"$FAKE_STATE/run-args"
    n=$(( $(cat "$FAKE_STATE/count" 2>/dev/null || echo 0) + 1 )); echo "$n" >"$FAKE_STATE/count"
    ok() { mkdir -p screener/output/fake-x; echo "# done" >screener/output/fake-x/summary.md; exit 0; }
    case "$FAKE_RUN" in
      ok) ok ;;
      fail) echo "  ! the county parcel layer is unavailable (HTTP 404)."; exit 1 ;;
      fail2) [ "$n" -gt 2 ] && ok; echo "  ! flaky"; exit 1 ;;
      refresh) [[ "$*" == *--refresh* ]] && ok; echo "  ! bad answer; re-run later, or with --refresh"; exit 1 ;;
      warn) mkdir -p screener/output/fake-x
            echo "- SOIL SERVICE FAILURES: re-run later" >screener/output/fake-x/summary.md; exit 0 ;;
    esac ;;
esac
exit 0
EOF

# Fake claude: acts in its working directory (the worktree) per $FAKE_CLAUDE.
cat >"$ROOT/fakeclaude" <<'EOF'
#!/usr/bin/env bash
echo "$*" >"$FAKE_STATE/claude-args"
case "$FAKE_CLAUDE" in
  fix)     echo "URL = 'new'" >>screener/fake.py ;;
  rank)    echo "# nudge" >>screener/rank.py ;;
  outside) echo "x" >>README.md ;;
  test)    echo "# weakened" >>screener/tests/test_fake.py ;;
  newtest) echo "# new" >screener/tests/test_new.py; echo "URL = 'new'" >>screener/fake.py ;;
  none)    ;;
esac
printf 'County moved its parcel layer\nEvidence: 404 at the old URL.\n' >screener-fix-DIAGNOSIS.md
EOF
chmod +x "$ROOT/fakepy" "$ROOT/fakeclaude"

# new_case NAME: fresh repo + state; exports the supervisor's config.
new_case() {
  local d="$ROOT/$1"
  mkdir -p "$d/repo/screener/tests" "$d/state" "$d/fake"
  (cd "$d/repo" && git init -q -b main && git config user.email t@t && git config user.name t &&
   printf '[run]\ncounty = "fake"\n' >screener/settings.toml && : >screener/requirements.txt &&
   echo "URL = 'old'" >screener/fake.py && echo "x = 1" >screener/rank.py &&
   echo "# t" >screener/tests/test_fake.py && echo "readme" >README.md &&
   printf 'screener/output/\nscreener/cache/\n' >.gitignore && git add -A && git commit -qm init)
  export SCREENER_OPS_CONFIG=/nonexistent REPO_DIR="$d/repo" STATE_DIR="$d/state" \
         PYTHON="$ROOT/fakepy" CLAUDE_BIN="$ROOT/fakeclaude" FAKE_STATE="$d/fake" \
         PULL=0 SOILS_QUIET=0 SLEEP=true RETRY_WAITS="5 5" NTFY_URL="" CLAUDE_FIX=1 FIX_PUSH=0
  CASE="$d"
}
alerts() { cat "$CASE/state/logs/alerts.log" 2>/dev/null; }
branches() { git -C "$CASE/repo" branch --list 'screener-fix/*'; }

echo "success on the first try"
new_case ok; FAKE_RUN=ok FAKE_CLAUDE=none "$SUP" 2>/dev/null; rc=$?
check "exit 0" '[ $rc -eq 0 ]'
check "one run" '[ "$(cat $CASE/fake/count)" = 1 ]'
check "summary says 1 ok" 'alerts | grep -q "1 ok, 0 failed"'
check "Claude not called" '[ ! -f $CASE/fake/claude-args ]'

echo "two failures, then success"
new_case flaky; FAKE_RUN=fail2 FAKE_CLAUDE=none "$SUP" 2>/dev/null; rc=$?
check "exit 0" '[ $rc -eq 0 ]'
check "three runs" '[ "$(cat $CASE/fake/count)" = 3 ]'
check "Claude not called" '[ ! -f $CASE/fake/claude-args ]'

echo "a --refresh stop is retried with --refresh"
new_case refresh; FAKE_RUN=refresh FAKE_CLAUDE=none "$SUP" 2>/dev/null; rc=$?
check "exit 0" '[ $rc -eq 0 ]'
check "second run had --refresh" 'sed -n 2p $CASE/fake/run-args | grep -q -- --refresh'
check "first run did not" '! sed -n 1p $CASE/fake/run-args | grep -q -- --refresh'

echo "extra arguments reach the screener"
new_case args; FAKE_RUN=ok FAKE_CLAUDE=none "$SUP" --limit 50 2>/dev/null
check "--limit 50 passed" 'grep -q -- "--limit 50" $CASE/fake/run-args'

echo "warnings in summary.md get an alert"
new_case warn; FAKE_RUN=warn FAKE_CLAUDE=none "$SUP" 2>/dev/null
check "warning alert" 'alerts | grep -q "done with warnings"'

echo "persistent failure, Claude fixes the county module"
new_case fix; FAKE_RUN=fail FAKE_CLAUDE=fix "$SUP" 2>/dev/null; rc=$?
check "exit 1" '[ $rc -eq 1 ]'
check "three runs before hand-off" '[ "$(cat $CASE/fake/count)" = 3 ]'
check "Claude ran in dontAsk mode" 'grep -q -- "--permission-mode dontAsk" $CASE/fake/claude-args'
check "fix branch exists" '[ -n "$(branches)" ]'
b="$(branches | tr -d " *")"
check "branch changes only fake.py" '[ "$(git -C $CASE/repo diff --name-only main "$b")" = "screener/fake.py" ]'
check "commit carries the diagnosis" 'git -C $CASE/repo log -1 --format=%B "$b" | grep -q "County moved its parcel layer"'
check "diagnosis file not committed" '! git -C $CASE/repo show --name-only "$b" | grep -q DIAGNOSIS'
check "cache symlink not committed" '! git -C $CASE/repo show --name-only "$b" | grep -q cache'
check "main checkout untouched" '[ -z "$(git -C $CASE/repo status --porcelain)" ] && grep -q old $CASE/repo/screener/fake.py'
check "worktree removed" '[ -z "$(ls -A $CASE/state/worktrees)" ]'
check "fix-ready alert" 'alerts | grep -q "fix ready for review"'

for mode in rank outside test; do
  echo "Claude edits a forbidden file ($mode)"
  new_case "forbid-$mode"; FAKE_RUN=fail FAKE_CLAUDE=$mode "$SUP" 2>/dev/null
  check "fix refused" 'alerts | grep -q "fix refused"'
  check "no fix branch" '[ -z "$(branches)" ]'
  check "patch kept for reading" 'ls $CASE/state/logs/*-fix.patch >/dev/null 2>&1'
done

echo "Claude adds a new test with its fix (allowed)"
new_case newtest; FAKE_RUN=fail FAKE_CLAUDE=newtest "$SUP" 2>/dev/null
check "fix branch exists" '[ -n "$(branches)" ]'

echo "Claude makes no change (outage)"
new_case none; FAKE_RUN=fail FAKE_CLAUDE=none "$SUP" 2>/dev/null
check "diagnosis-only alert" 'alerts | grep -q "no code fix"'
check "no fix branch" '[ -z "$(branches)" ]'

echo "hand-off off"
new_case off; FAKE_RUN=fail FAKE_CLAUDE=fix CLAUDE_FIX=0 "$SUP" 2>/dev/null
check "plain failure alert" 'alerts | grep -q "no AI hand-off\|hand-off is off"'
check "Claude not called" '[ ! -f $CASE/fake/claude-args ]'

echo "a second supervisor exits while one holds the lock"
new_case lock; mkdir -p "$CASE/state"
exec 8>"$CASE/state/supervise.lock"; flock -n 8
FAKE_RUN=ok FAKE_CLAUDE=none "$SUP" 2>/dev/null; rc=$?
exec 8>&-
check "exit 0 without running" '[ $rc -eq 0 ] && [ ! -f $CASE/fake/count ]'

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
