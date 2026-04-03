#!/usr/bin/env bash
set -euo pipefail

# Review cycle: re-evaluate oldest unreviewed ideas in round-robin batches.
# Runs via systemd timer. Uses flock to prevent concurrent runs.

cd /opt/project-forge

echo "$(date): Running idea review cycle (batch of 10)..."

flock -n /tmp/project-forge-review.lock \
  python3 -c "
import asyncio, logging, os, sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')

from project_forge.storage.db import Database
from project_forge.cron.review_runner import run_review_cycle

async def main():
    db = Database(Path(os.environ.get('FORGE_DB_PATH', 'data/forge.db')))
    await db.connect()
    result = await run_review_cycle(db, batch_size=10, min_age_days=7)
    reviewed = result['reviewed']
    kills = sum(1 for r in result['results'] if r.get('verdict') == 'kill')
    errors = sum(1 for r in result['results'] if r.get('status') == 'error')
    print(f'Review cycle: {reviewed} reviewed, {kills} killed, {errors} errors')
    await db.close()

asyncio.run(main())
"

echo "$(date): Review cycle complete."
