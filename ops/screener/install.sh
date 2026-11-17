#!/usr/bin/env bash
# Install the weekly screener timer and the Claude hand-off sandbox. Run as
# root on the server, after the repository is cloned to
# /home/screener/Land-Research (see README.md). Safe to run again.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_USER=screener
FIX_USER=screener-fix
FIX_DIR=/srv/screener-fix
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
