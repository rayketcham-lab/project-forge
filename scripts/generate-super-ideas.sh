#!/bin/bash
# Daily super idea synthesis - rotated category perspective
# Usage: generate-super-ideas.sh [slot_number]
#   slot 0 = PQC & Crypto
#   slot 1 = Standards & Compliance
#   slot 2 = Attack & Defense
#   slot 3 = Platform & DevOps
#   slot 4 = Privacy & Market
set -euo pipefail

cd /opt/project-forge

if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

SLOT="${1:-0}"
export FORGE_DB_PATH="${FORGE_DB_PATH:-/opt/project-forge/data/forge.db}"

echo "$(date): Generating super idea with slot $SLOT..."
python3 -c "
import asyncio
from pathlib import Path
from project_forge.engine.super_ideas import SuperIdeaGenerator, DAILY_ROTATION
from project_forge.storage.db import Database

async def main():
    slot = $SLOT
    rot = DAILY_ROTATION[slot % len(DAILY_ROTATION)]
    print(f'Perspective: {rot[\"label\"]} -- {rot[\"perspective\"]}')
    db = Database(Path('$FORGE_DB_PATH'))
    await db.connect()
    gen = SuperIdeaGenerator(db)
    si = await gen.generate_seeded(slot=slot)
    if si:
        print(f'Generated: [{si.impact_score:.2f}] {si.name} ({len(si.component_idea_ids)} components)')
    else:
        print('No super idea generated (not enough ideas or clusters)')
    await db.close()

asyncio.run(main())
"
