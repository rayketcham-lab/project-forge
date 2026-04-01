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

echo "$(date): Running self-introspection..."
exec flock -n /tmp/project-forge-introspect.lock python3 -m project_forge.cron.introspect_runner
