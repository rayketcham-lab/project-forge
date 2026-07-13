"""Cron entry point for self-introspection — generates self-improvement ideas."""

import asyncio
import logging
import os
import sys

from project_forge.config import settings
from project_forge.engine.dedup import filter_and_save
from project_forge.engine.introspect import (
    build_introspection_prompt,
    gather_generation_signals,
    gather_self_context,
    validate_generation_patch,
)
from project_forge.engine.quality_review import review_idea
from project_forge.models import IdeaCategory
from project_forge.storage.db import Database

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _recent_commit_subjects(limit: int = 50) -> list[str]:
    """Recent commit subjects for the Think Tank reconciler; [] on any failure."""
    import subprocess
    from pathlib import Path

    try:
        out = subprocess.run(
            ["git", "log", "--format=%s", f"-{limit}"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(Path(__file__).parents[3]),
        )
    except Exception:
        return []
    if out.returncode != 0:
        return []
    return [line for line in out.stdout.splitlines() if line.strip()]


def _pick_introspect_mode(recent_si: list) -> str:
    """Alternate code-fix and generation modes across fires (#90).

    The generation-mode prompt carries engine telemetry (saturation,
    filter rate, novelty, coverage gaps) that the code-fix prompt lacks;
    flipping on the latest idea's stamp gives strict alternation with no
    extra state.
    """
    if recent_si and recent_si[0].generation_mode == "introspect-code-fix":
        return "generation"
    return "code-fix"


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

    mode = _pick_introspect_mode(recent_si)
    context = gather_self_context()
    generation_signals = await gather_generation_signals(db) if mode == "generation" else None
    prompt = build_introspection_prompt(
        context,
        recent_names,
        mode=mode,
        generation_signals=generation_signals,
    )

    idea = await generator.generate(
        category=IdeaCategory.SELF_IMPROVEMENT,
        prompt_override=prompt,
    )
    idea.generation_mode = f"introspect-{mode}"

    if mode == "generation" and not validate_generation_patch(idea):
        logger.warning("Rejected generation-mode SI idea '%s': failed patch validation", idea.name)
        return None

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
        generator = None

        if api_key:
            from project_forge.engine.generator import IdeaGenerator

            generator = IdeaGenerator(api_key=api_key)
            logger.info("Introspection using Anthropic API")
        else:
            # No API key — try Claude Code CLI before falling back to static.
            from project_forge.engine.llm_backend import resolve_backend

            backend = resolve_backend(force="claude_code")
            if backend is not None:
                from project_forge.engine.generator import LLMBackendIdeaGenerator

                generator = LLMBackendIdeaGenerator(backend)
                logger.info("Introspection using backend: %s", backend.name)
            else:
                logger.info("No LLM backend — using static introspection")

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
