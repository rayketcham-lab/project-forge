"""CLI entry point for hourly horizontal expansion."""

import asyncio
import logging
import sys

from project_forge.config import settings
from project_forge.storage.db import Database

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def _run() -> None:
    db = Database(settings.db_path)
    await db.connect()
    try:
        from project_forge.cron.horizontal import run_horizontal_cycle

        ideas = await run_horizontal_cycle(db)
        logger.info("Horizontal expansion complete: %d ideas generated", len(ideas))
        for idea in ideas:
            logger.info("  - %s (%s, %.2f)", idea.name, idea.category.value, idea.feasibility_score)
    except Exception:
        logger.exception("Horizontal expansion failed")
        sys.exit(1)
    finally:
        await db.close()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
