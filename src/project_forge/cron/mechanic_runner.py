"""One-shot Forge Mechanic runner (#100).

Runs a single mechanic cycle in its OWN process so the web server and the
scheduler never block on the (minutes-long, subprocess-heavy) agent run.
Both the disarmed cadence and the operator's "Run now" button fan out to
this via `spawn_mechanic_run()`.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from pathlib import Path

from project_forge.config import settings
from project_forge.engine.mechanic import run_mechanic_cycle
from project_forge.storage.db import Database

logger = logging.getLogger(__name__)

# .../src/project_forge/cron/mechanic_runner.py -> repo root
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


async def run_once() -> dict:
    """Open a fresh DB connection, run one mechanic cycle, report."""
    db = Database(settings.db_path)
    await db.connect()
    try:
        result = await run_mechanic_cycle(db)
        logger.info(
            "Mechanic: %s — %s (%s)",
            result.status,
            result.idea_name or "-",
            result.pr_url or result.detail or "",
        )
        return {"status": result.status, "idea": result.idea_name, "pr_url": result.pr_url}
    finally:
        await db.close()


def spawn_mechanic_run() -> None:
    """Fire-and-forget: launch one mechanic cycle as a detached process.
    Non-blocking and isolated — the caller (server/scheduler) returns
    immediately and the PR shows up in the review panel when it lands."""
    subprocess.Popen(  # noqa: S603 — fixed argv, no shell
        [sys.executable, "-m", "project_forge.cron.mechanic_runner"],
        cwd=str(_PROJECT_ROOT),
        start_new_session=True,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_once())


if __name__ == "__main__":
    main()
