"""Cron entry point for self-introspection — generates self-improvement ideas."""

import asyncio
import logging
import os
import sys

from project_forge.config import settings
from project_forge.engine.dedup import filter_and_save
from project_forge.engine.introspect import build_introspection_prompt, gather_self_context
from project_forge.engine.quality_review import review_idea
from project_forge.models import IdeaCategory
from project_forge.storage.db import Database

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def run_introspect_cycle(db: Database, generator=None) -> "Idea":  # noqa: F821
    """Run one introspection cycle.

    When *generator* is ``None`` (no API key), falls back to static analysis
    that requires no external services.
    """
    if generator is None:
        # Static fallback — no API key needed
        from project_forge.engine.static_introspect import generate_static_proposals

        proposals = generate_static_proposals()
        if not proposals:
            logger.info("Static introspection found no proposals")
            return None

        # Pick the first proposal that passes dedup
        for idea in proposals:
            _, accepted, reason = await filter_and_save(idea, db)
            if accepted:
                logger.info("Static introspection stored: %s", idea.name)
                return idea
            logger.info("Static proposal '%s' filtered: %s", idea.name, reason)
        return None

    # LLM-powered introspection (requires API key)
    recent_si = await db.list_ideas(category=IdeaCategory.SELF_IMPROVEMENT, limit=10)
    recent_names = [i.name for i in recent_si]

    context = gather_self_context()
    prompt = build_introspection_prompt(context, recent_names)

    idea = await generator.generate(
        category=IdeaCategory.SELF_IMPROVEMENT,
        prompt_override=prompt,
    )

    # Quality review: reject low-quality or new-project proposals
    result = review_idea(idea)
    if not result.passed:
        logger.warning("Rejected SI idea '%s': %s", idea.name, "; ".join(result.reasons))
        return None

    _, accepted, reason = await filter_and_save(idea, db)
    if not accepted:
        logger.info("Introspection idea '%s' filtered: %s", idea.name, reason)
        return None
    logger.info("Introspection generated: %s (score: %.2f)", idea.name, idea.feasibility_score)
    return idea


async def _run() -> None:
    db = Database(settings.db_path)
    await db.connect()
    try:
        api_key = settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if api_key:
            from project_forge.engine.generator import IdeaGenerator

            generator = IdeaGenerator(api_key=api_key)
        else:
            logger.info("No API key — using static introspection")
            generator = None

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
