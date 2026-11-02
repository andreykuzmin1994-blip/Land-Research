#!/usr/bin/env bash
# One-time setup inside WSL Ubuntu. Install.bat runs this with the path of
# settings.txt. Safe to run again: every step skips what is already done.
set -euo pipefail

SETTINGS=${1:?usage: bootstrap.sh <settings.txt>}
REPO="$HOME/Land-Research"
export PATH="$HOME/.local/bin:$PATH"

repo_url=$(sed -e 's/\r$//' "$SETTINGS" | grep -E '^[[:space:]]*REPO_URL[[:space:]]*=' | tail -1 \
    | sed -E 's/^[[:space:]]*REPO_URL[[:space:]]*=[[:space:]]*//; s/[[:space:]]+$//')
[ -n "$repo_url" ] || repo_url=https://github.com/andreykuzmin1994-blip/Land-Research.git

echo "==> 1/5 Installing tools (Ubuntu may ask for your Ubuntu password)"
sudo apt-get update -qq
sudo apt-get install -y -qq git gh make tmux python3 python3-venv python3-pip curl

echo "==> 2/5 Signing in to GitHub"
if ! gh auth status >/dev/null 2>&1; then
    echo "    A code appears below. Open the link in your browser and enter it."
    gh auth login --hostname github.com --git-protocol https --web
fi
gh auth setup-git
if ! git config --global user.email >/dev/null; then
    login=$(gh api user --jq .login)
    git config --global user.name "$login"
    git config --global user.email "$(gh api user --jq .id)+$login@users.noreply.github.com"
fi

echo "==> 3/5 Downloading the repository to ~/Land-Research"
[ -d "$REPO/.git" ] || git clone "$repo_url" "$REPO"
cd "$REPO"
git config core.hooksPath .githooks
if [ ! -f home_machine/autopilot.sh ]; then
    echo "ERROR: home_machine/autopilot.sh is not on the main branch yet."
    echo "       Merge the branch that adds home_machine/ into main, then run Install.bat again."
    exit 1
fi

echo "==> 4/5 Installing Python packages"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt -r screener/requirements.txt

echo "==> 5/5 Installing Claude Code and checking the database"
command -v claude >/dev/null || curl -fsSL https://claude.ai/install.sh | bash
bash home_machine/autopilot.sh configure "$SETTINGS"

echo
echo "Last step: sign in to Claude Code. A Claude prompt opens next."
echo "Follow its sign-in steps, then type /exit and press Enter."
read -r -p "Press Enter to open Claude Code... " _
claude || true
echo
echo "==> Setup complete."
