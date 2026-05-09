#!/bin/bash
# Autonomous self-improvement runner (with flock to prevent overlap)
set -euo pipefail

cd /opt/project-forge

if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

export FORGE_DB_PATH="${FORGE_DB_PATH:-/opt/project-forge/data/forge.db}"

# Enable Claude Code CLI as LLM backend for code patches.
export FORGE_LLM_BACKEND="${FORGE_LLM_BACKEND:-claude_code}"
export FORGE_LLM_MODEL="${FORGE_LLM_MODEL:-sonnet}"

echo "$(date): Running self-improvement cycle..."
exec flock -n /tmp/project-forge-self-improve.lock python3 -m project_forge.cron.self_improve_runner
