#!/bin/bash
# Hourly horizontal expansion (with flock to prevent overlap)
set -euo pipefail

cd /opt/project-forge

if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

export FORGE_DB_PATH="${FORGE_DB_PATH:-/opt/project-forge/data/forge.db}"

# Enable Sonnet-driven super-idea cluster naming (Phase 6 reasoning).
# Without these env vars the engine slot-fills the same 5 base names
# every cycle and dedup blocks growth. Defaults can be overridden in
# /opt/project-forge/.env if you want to switch to anthropic-api.
export FORGE_SUPER_REASONING="${FORGE_SUPER_REASONING:-1}"
export FORGE_LLM_BACKEND="${FORGE_LLM_BACKEND:-claude_code}"
export FORGE_LLM_MODEL="${FORGE_LLM_MODEL:-sonnet}"

echo "$(date): Running horizontal expansion..."
exec flock -n /tmp/project-forge-expand.lock python3 -m project_forge.cron.expand_runner
