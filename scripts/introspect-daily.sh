#!/bin/bash
# Daily self-introspection (with flock to prevent overlap)
set -euo pipefail

cd /opt/project-forge

if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

export FORGE_DB_PATH="${FORGE_DB_PATH:-/opt/project-forge/data/forge.db}"

# Enable Claude Code CLI as LLM backend for richer SI proposals.
# Without these the runner falls back to static heuristics ("Decompose X",
# "Add tests for X" only). Override in /opt/project-forge/.env if needed.
export FORGE_LLM_BACKEND="${FORGE_LLM_BACKEND:-claude_code}"
export FORGE_LLM_MODEL="${FORGE_LLM_MODEL:-sonnet}"

echo "$(date): Running self-introspection..."
exec flock -n /tmp/project-forge-introspect.lock python3 -m project_forge.cron.introspect_runner
