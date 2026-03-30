#!/bin/bash
# One-shot idea generation for cron
set -euo pipefail

cd /opt/project-forge

# Load API key from .env if it exists
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

export FORGE_DB_PATH="${FORGE_DB_PATH:-/opt/project-forge/data/forge.db}"

if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
    echo "$(date): ANTHROPIC_API_KEY not set. Create /opt/project-forge/.env with ANTHROPIC_API_KEY=sk-ant-..."
    exit 0
fi

exec python3 -m project_forge.cron.runner
