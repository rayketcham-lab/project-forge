"""Cron entry point for self-introspection — generates self-improvement ideas."""

import asyncio
import logging
import os
import sys

from project_forge.config import settings
from project_forge.engine.introspect import build_introspection_prompt, gather_self_context
from project_forge.models import IdeaCategory
from project_forge.storage.db import Database

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def run_introspect_cycle(db: Database, generator) -> "Idea":  # noqa: F821
    """Run one introspection cycle: gather context, generate self-improvement idea, store it."""
    # Get recent self-improvement idea names to avoid duplicates
    recent_si = await db.list_ideas(category=IdeaCategory.SELF_IMPROVEMENT, limit=10)
    recent_names = [i.name for i in recent_si]

    # Gather codebase context
    context = gather_self_context()

    # Build the introspection prompt
    prompt = build_introspection_prompt(context, recent_names)

    # Generate idea using the prompt
    idea = await generator.generate(
        category=IdeaCategory.SELF_IMPROVEMENT,
        prompt_override=prompt,
    )

    await db.save_idea(idea)
    logger.info("Introspection generated: %s (score: %.2f)", idea.name, idea.feasibility_score)
    return idea


async def _run() -> None:
    db = Database(settings.db_path)
    await db.connect()
    try:
        api_key = settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            logger.error("No API key available for introspection")
            sys.exit(1)

        from project_forge.engine.generator import IdeaGenerator

        generator = IdeaGenerator(api_key=api_key)
        await run_introspect_cycle(db, generator)
    except Exception:
        logger.exception("Introspection cycle failed")
        sys.exit(1)
    finally:
        await db.close()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
