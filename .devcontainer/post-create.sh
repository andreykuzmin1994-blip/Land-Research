#!/usr/bin/env bash
# .devcontainer/post-create.sh — runs ONCE per Codespace creation.
#
# Wires up everything that survives the lifetime of the codespace:
#   - pip-installed Python deps
#   - the pre-commit hook path (per CLAUDE.md "Developer setup")
#
# Per-start hydration of .env from Codespaces secrets is in
# .devcontainer/post-start.sh so secret rotations pick up automatically.

set -euo pipefail

cd "${CODESPACE_VSCODE_FOLDER:-/workspaces/Land-Research}"

echo "==> installing Python requirements"
pip install --user --quiet -r requirements.txt

echo "==> activating .githooks/ pre-commit hook"
git config core.hooksPath .githooks

echo "==> post-create complete"
