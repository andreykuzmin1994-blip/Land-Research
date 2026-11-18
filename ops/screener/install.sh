#!/usr/bin/env bash
# Install the weekly screener timer and the Claude hand-off sandbox. Run as
# root on the server, after the repository is cloned to
# /home/screener/Land-Research (see README.md). Safe to run again.
#
#   install.sh                  the timer and the fix hand-off's sandbox user
#   install.sh --lead-checks    also the two accounts of the lead checks
#                               (README.md, "Lead checks"); they stay off
#                               until INVESTIGATE=1 is set in the config
#   install.sh --agenda-watch   also the account and the daily timer of the
#                               agenda watch (README.md, "Agenda watch"); it
#                               does nothing until you write its sources file
set -euo pipefail

LEAD_CHECKS=0
AGENDA_WATCH=0
for arg in "$@"; do
  case "$arg" in
    --lead-checks) LEAD_CHECKS=1 ;;
    --agenda-watch) AGENDA_WATCH=1 ;;
    *) echo "usage: $0 [--lead-checks] [--agenda-watch]"; exit 2 ;;
  esac
done

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_USER=screener
FIX_USER=screener-fix
FIX_DIR=/srv/screener-fix
LEAD_USER=screener-lead      # runs the two lead-check agents
LEAD_DIR=/srv/screener-lead
CHECK_USER=screener-check    # runs the quote check; no agent ever runs as it
CHECK_DIR=/srv/screener-check
AGENDA_USER=screener-agenda  # fetches and reads agenda documents; owns nothing
AGENDA_DIR=/srv/screener-agenda
REPO="/home/$RUN_USER/Land-Research"

[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo $0)"; exit 1; }
id "$RUN_USER" >/dev/null 2>&1 || { echo "create the '$RUN_USER' user first (README.md step 2)"; exit 1; }
[ -d "$REPO/.git" ] || { echo "clone the repo to $REPO first"; exit 1; }

# The run user's home stays private: the fixer must not read its config,
# SSH keys or checkout.
chmod 750 "/home/$RUN_USER"

# The fixer: its own user, NOT in the run user's group. The run user joins
# the fixer's group only to hand files over through $FIX_DIR.
if ! id "$FIX_USER" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" "$FIX_USER"
fi
chmod 750 "/home/$FIX_USER"
usermod -aG "$FIX_USER" "$RUN_USER"
# The handover folder belongs to the run user: the fixer can work inside the
# copies made for it but cannot create, rename or plant entries here.
install -d "$FIX_DIR"
chown "$RUN_USER:$FIX_USER" "$FIX_DIR"
chmod 2750 "$FIX_DIR"

# No cron or at jobs for the fixer: nothing it runs may outlive the hand-off.
for f in /etc/cron.deny /etc/at.deny; do
  touch "$f"
  grep -qx "$FIX_USER" "$f" || echo "$FIX_USER" >>"$f"
done

# The run user may run commands as the fixer (a step down, never up).
echo "$RUN_USER ALL=($FIX_USER) NOPASSWD: ALL" >/etc/sudoers.d/screener-fix
chmod 440 /etc/sudoers.d/screener-fix
visudo -cf /etc/sudoers.d/screener-fix >/dev/null

# The fixer's own Python for trial runs and tests.
if [ ! -x "/home/$FIX_USER/.venv/bin/python" ]; then
  sudo -u "$FIX_USER" -H python3 -m venv "/home/$FIX_USER/.venv"
fi
# (A copy it can read: sudo closes inherited descriptors, so no <(...).)
install -m 644 "$REPO/screener/requirements.txt" "/home/$FIX_USER/.screener-requirements.txt"
sudo -u "$FIX_USER" -H "/home/$FIX_USER/.venv/bin/pip" install -q -r "/home/$FIX_USER/.screener-requirements.txt"

if [ "$LEAD_CHECKS" = 1 ]; then
  # The agents' account: a normal user (Claude Code logs in under its home),
  # NOT the fixer's, so an agent that reads the web can never reach the
  # code hand-off. Its home is private.
  if ! id "$LEAD_USER" >/dev/null 2>&1; then
    adduser --disabled-password --gecos "" "$LEAD_USER"
  fi
  chmod 750 "/home/$LEAD_USER"
  # The quote check's account: no home, no shell, nothing of its own. It
  # only ever runs the system Python on a copy of quotecheck.py.
  if ! id "$CHECK_USER" >/dev/null 2>&1; then
    adduser --system --group --no-create-home --shell /usr/sbin/nologin "$CHECK_USER"
  fi
  usermod -aG "$LEAD_USER","$CHECK_USER" "$RUN_USER"
  # Handover folders, owned by the run user: a sandbox user works inside
  # the folder made for it and cannot create or rename entries beside it.
  for pair in "$LEAD_DIR:$LEAD_USER" "$CHECK_DIR:$CHECK_USER"; do
    install -d "${pair%%:*}"
    chown "$RUN_USER:${pair##*:}" "${pair%%:*}"
    chmod 2750 "${pair%%:*}"
  done
  for f in /etc/cron.deny /etc/at.deny; do
    for u in "$LEAD_USER" "$CHECK_USER"; do
      grep -qx "$u" "$f" || echo "$u" >>"$f"
    done
  done
  printf '%s\n' "$RUN_USER ALL=($LEAD_USER) NOPASSWD: ALL" "$RUN_USER ALL=($CHECK_USER) NOPASSWD: ALL" \
    >/etc/sudoers.d/screener-lead-checks
  chmod 440 /etc/sudoers.d/screener-lead-checks
  visudo -cf /etc/sudoers.d/screener-lead-checks >/dev/null
  command -v pdftotext >/dev/null 2>&1 || apt-get install -y poppler-utils ||
    echo "could not install poppler-utils (pdftotext): install it by hand, the lead checks need it"
  echo "lead-check accounts ready: $LEAD_USER (agents), $CHECK_USER (quote check)."
  if [ ! -x "/home/$LEAD_USER/.local/bin/claude" ]; then
    echo "Next: install and log in to Claude Code as $LEAD_USER (README.md, Lead checks):"
    echo "  sudo -iu $LEAD_USER"
  fi
  echo "Lead checks stay off until INVESTIGATE=1 is in /home/$RUN_USER/.config/screener-ops.env."
fi

if [ ! -f "/home/$RUN_USER/.config/screener-ops.env" ]; then
  install -d -o "$RUN_USER" -g "$RUN_USER" "/home/$RUN_USER/.config"
  install -m 600 -o "$RUN_USER" -g "$RUN_USER" "$HERE/screener-ops.env.example" \
    "/home/$RUN_USER/.config/screener-ops.env"
  echo "wrote /home/$RUN_USER/.config/screener-ops.env (edit NTFY_URL there)"
fi

install -m 644 "$HERE/screener-supervise.service" /etc/systemd/system/
install -m 644 "$HERE/screener-supervise.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now screener-supervise.timer
systemctl list-timers screener-supervise.timer --no-pager

if [ "$AGENDA_WATCH" = 1 ]; then
  # The account that makes the requests and reads the PDFs: no home, no
  # shell, nothing of its own, and not an account any other part uses.
  if ! id "$AGENDA_USER" >/dev/null 2>&1; then
    adduser --system --group --no-create-home --shell /usr/sbin/nologin "$AGENDA_USER"
  fi
  usermod -aG "$AGENDA_USER" "$RUN_USER"
  install -d "$AGENDA_DIR"
  chown "$RUN_USER:$AGENDA_USER" "$AGENDA_DIR"
  chmod 2750 "$AGENDA_DIR"
  for f in /etc/cron.deny /etc/at.deny; do
    grep -qx "$AGENDA_USER" "$f" || echo "$AGENDA_USER" >>"$f"
  done
  echo "$RUN_USER ALL=($AGENDA_USER) NOPASSWD: ALL" >/etc/sudoers.d/screener-agenda-watch
  chmod 440 /etc/sudoers.d/screener-agenda-watch
  visudo -cf /etc/sudoers.d/screener-agenda-watch >/dev/null
  command -v pdftotext >/dev/null 2>&1 || apt-get install -y poppler-utils ||
    echo "could not install poppler-utils (pdftotext): install it by hand, the agenda watch needs it"
  install -m 644 "$HERE/screener-agenda.service" /etc/systemd/system/
  install -m 644 "$HERE/screener-agenda.timer" /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable --now screener-agenda.timer
  systemctl list-timers screener-agenda.timer --no-pager
  echo "agenda watch installed. It does nothing until /home/$RUN_USER/.config/screener-agenda-sources.json"
  echo "exists: copy $HERE/agenda_sources.example.json there (chmod 600), and check each site's"
  echo "robots.txt and terms yourself before you list it (README.md, Agenda watch)."
fi

if [ ! -x "/home/$FIX_USER/.local/bin/claude" ]; then
  echo
  echo "Next: install and log in to Claude Code as $FIX_USER (README.md step 6):"
  echo "  sudo -iu $FIX_USER"
fi
