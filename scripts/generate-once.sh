#!/bin/bash
# One-shot idea generation for cron
# Uses Claude API if ANTHROPIC_API_KEY is set, otherwise auto-scans from seed data
set -euo pipefail

cd /opt/project-forge

# Load .env if it exists
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

export FORGE_DB_PATH="${FORGE_DB_PATH:-/opt/project-forge/data/forge.db}"

echo "$(date): Running idea generation..."
exec python3 -m project_forge.cron.runner
