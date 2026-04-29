#!/bin/bash
# Start the Project Forge web dashboard
set -euo pipefail

cd "$(dirname "$0")/.."

export FORGE_DB_PATH="${FORGE_DB_PATH:-data/forge.db}"

exec python3 -m uvicorn project_forge.web.app:app \
    --host 0.0.0.0 \
    --port "${FORGE_PORT:-55443}" \
    --log-level info \
    --reload \
    --reload-dir src/project_forge
