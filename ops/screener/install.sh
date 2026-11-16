#!/usr/bin/env bash
# Install the weekly screener timer. Run as root on the server, after the
# repository is cloned to /home/screener/Land-Research (see README.md).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_NAME=screener
HOME_DIR="/home/$USER_NAME"

[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo $0)"; exit 1; }
id "$USER_NAME" >/dev/null 2>&1 || { echo "create the '$USER_NAME' user first (README.md step 2)"; exit 1; }
[ -d "$HOME_DIR/Land-Research/.git" ] || { echo "clone the repo to $HOME_DIR/Land-Research first"; exit 1; }

if [ ! -f "$HOME_DIR/.config/screener-ops.env" ]; then
  install -d -o "$USER_NAME" -g "$USER_NAME" "$HOME_DIR/.config"
  install -m 600 -o "$USER_NAME" -g "$USER_NAME" "$HERE/screener-ops.env.example" "$HOME_DIR/.config/screener-ops.env"
  echo "wrote $HOME_DIR/.config/screener-ops.env (edit NTFY_URL there)"
fi

install -m 644 "$HERE/screener-supervise.service" /etc/systemd/system/
install -m 644 "$HERE/screener-supervise.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now screener-supervise.timer
systemctl list-timers screener-supervise.timer --no-pager
