"""CLI entry point for cron-based idea generation."""

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


async def _run():
    db = Database(settings.db_path)
    await db.connect()
    try:
        from project_forge.engine.llm_backend import resolve_backend

        if resolve_backend() is not None:
            # Use the LLM backend for high-quality generation
            from project_forge.cron.scheduler import run_full_cycle
            from project_forge.engine.generator import IdeaGenerator

            generator = IdeaGenerator()
            idea = await run_full_cycle(db, generator)
            logger.info("API-generated: %s (score: %.2f)", idea.name, idea.feasibility_score)
        else:
            # Use local generation from seed data
            from project_forge.cron.auto_scan import run_auto_scan

            ideas = await run_auto_scan(db, count=15)
            logger.info("Auto-scan generated %d ideas locally", len(ideas))
            for idea in ideas:
                logger.info("  - %s (%s, %.2f)", idea.name, idea.category.value, idea.feasibility_score)
    except Exception:
        logger.exception("Generation cycle failed")
        sys.exit(1)
    finally:
        await db.close()


def main():
    asyncio.run(_run())


if __name__ == "__main__":
    main()
