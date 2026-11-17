#!/usr/bin/env bash
# Install the weekly screener timer and the Claude hand-off sandbox. Run as
# root on the server, after the repository is cloned to
# /home/screener/Land-Research (see README.md). Safe to run again.
#
#   install.sh                  the timer and the fix hand-off's sandbox user
#   install.sh --lead-checks    also the two accounts of the lead checks
#                               (README.md, "Lead checks"); they stay off
#                               until INVESTIGATE=1 is set in the config
set -euo pipefail

LEAD_CHECKS=0
for arg in "$@"; do
  case "$arg" in
    --lead-checks) LEAD_CHECKS=1 ;;
    *) echo "usage: $0 [--lead-checks]"; exit 2 ;;
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
  command -v pdftotext >/dev/null 2>&1 || apt-get install -y poppler-utils
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

if [ ! -x "/home/$FIX_USER/.local/bin/claude" ]; then
  echo
  echo "Next: install and log in to Claude Code as $FIX_USER (README.md step 6):"
  echo "  sudo -iu $FIX_USER"
fi
