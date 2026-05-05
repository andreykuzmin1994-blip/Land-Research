#!/usr/bin/env bash
# .devcontainer/post-start.sh — runs every time the Codespace starts.
#
# Materializes .env from the Codespaces secrets that are injected as env
# vars at container start. Idempotent: rewrites .env each start so a
# rotated DATABASE_URL secret picks up automatically without rebuilding
# the container.
#
# .env is gitignored (see .gitignore line 2). Real credentials never
# touch env.template — see reviews/02_setup_phase/00_setup_status.md
# "Incident — env.template credential leak" for the backstory and the
# pre-commit hook + CI workflow that enforce this.

set -euo pipefail

cd "${CODESPACE_VSCODE_FOLDER:-/workspaces/Land-Research}"

if [ -z "${DATABASE_URL:-}" ]; then
  cat <<'WARN'
==> WARNING: DATABASE_URL is not set in this codespace.

    Set it once as a User secret at:
      https://github.com/settings/codespaces

    OR as a repo-scoped Codespaces secret at:
      https://github.com/andreykuzmin1994-blip/land-research/settings/secrets/codespaces

    Use the Supabase Session pooler DSN (port 5432). Then either:
      - rebuild this container (Cmd/Ctrl-Shift-P -> "Rebuild Container"), OR
      - run 'bash .devcontainer/post-start.sh' manually after refreshing
        the codespace shell so the new secret env var is loaded.

    Without DATABASE_URL, 'make db-check', 'make verify', 'make loop'
    and 'make status' will all fail with 'DATABASE_URL not set'.

WARN
  exit 0
fi

# Write .env atomically: write to a temp file in the same dir, then mv.
tmpfile="$(mktemp .env.XXXXXX)"
trap 'rm -f "$tmpfile"' EXIT

cat > "$tmpfile" <<EOF
DATABASE_URL=$DATABASE_URL
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}
EOF

mv "$tmpfile" .env
trap - EXIT

echo "==> .env hydrated from Codespaces secrets ($(wc -l < .env) lines)"
