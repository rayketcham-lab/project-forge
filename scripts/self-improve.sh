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

echo "$(date): Running self-improvement cycle..."
exec flock -n /tmp/project-forge-self-improve.lock python3 -m project_forge.cron.self_improve_runner
