"""In-process scheduler for the FastAPI lifespan.

Owns autonomous cadences that used to live in `/etc/systemd/system/`
project-forge-*.timer units. Those are unreachable from the sandboxed
runtime (no DBUS bus, no sudo, no write access), so file writes + uvicorn
--reload is the only deploy path. The lifespan owns the loop; uvicorn
restarts free the cancelled task.

Currently scheduled:
- Self-introspection (daily by default; `FORGE_INTROSPECT_INTERVAL_HOURS`)
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta

from project_forge.storage.db import Database

logger = logging.getLogger(__name__)


def _interval_from_env(var: str, default_hours: float) -> timedelta:
    raw = os.environ.get(var)
    if raw is None:
        return timedelta(hours=default_hours)
    try:
        return timedelta(hours=float(raw))
    except ValueError:
        logger.warning("Invalid %s=%r; using default %sh", var, raw, default_hours)
        return timedelta(hours=default_hours)


INTROSPECT_INTERVAL = _interval_from_env("FORGE_INTROSPECT_INTERVAL_HOURS", 24.0)
INITIAL_DELAY = timedelta(seconds=float(os.environ.get("FORGE_SCHED_INITIAL_DELAY_SEC", "60")))


async def seconds_until_next_introspect(db: Database, interval: timedelta) -> float:
    """Time (s) until the next introspect fire should happen, based on DB.

    The watermark unions accepted SI ideas (`ideas.generated_at` where
    `category='self-improvement'`) with filtered SI attempts
    (`filtered_ideas.filtered_at` where `idea_category='self-improvement'`).
    A filtered attempt still proves the runner fired, so a streak of
    100%-dedup days does NOT cause the loop to retry every tick.
    """
    cursor = await db.db.execute(
        "SELECT MAX(ts) FROM ("
        "  SELECT generated_at AS ts FROM ideas WHERE category = 'self-improvement'"
        "  UNION ALL"
        "  SELECT filtered_at AS ts FROM filtered_ideas WHERE idea_category = 'self-improvement'"
        ")"
    )
    row = await cursor.fetchone()
    last_ts = row[0] if row else None

    if not last_ts:
        return 0.0

    last = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    next_fire = last + interval
    delay = (next_fire - datetime.now(UTC)).total_seconds()
    return max(0.0, delay)


async def _fire_introspect(db: Database) -> None:
    """Run one introspect cycle with the configured LLM backend.

    Intentionally a thin wrapper around `run_introspect_cycle` — the cron
    `_run()` entrypoint calls `sys.exit(1)` on failure which would crash
    uvicorn, so we replicate its body without the exit.
    """
    from project_forge.config import settings
    from project_forge.cron.introspect_runner import run_introspect_cycle

    api_key = settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    generator = None

    if api_key:
        from project_forge.engine.generator import IdeaGenerator

        generator = IdeaGenerator(api_key=api_key)
        logger.info("In-process introspect using Anthropic API")
    else:
        from project_forge.engine.llm_backend import resolve_backend

        backend = resolve_backend(force="claude_code")
        if backend is not None:
            from project_forge.engine.generator import LLMBackendIdeaGenerator

            generator = LLMBackendIdeaGenerator(backend)
            logger.info("In-process introspect using backend: %s", backend.name)
        else:
            logger.info("In-process introspect using static fallback (no LLM backend)")

    await run_introspect_cycle(db, generator)


async def introspect_tick(db: Database, interval: timedelta) -> None:
    """One iteration of the introspect loop.

    Errors are logged but never propagate — the loop survives them.
    """
    try:
        delay = await seconds_until_next_introspect(db, interval=interval)
        if delay > 0:
            return
        logger.info("In-process scheduler firing introspect")
        await _fire_introspect(db)
    except Exception:
        logger.exception("introspect_tick failed")


async def _introspect_loop(db: Database, tick_interval: float) -> None:
    """Background task body. Cancellation propagates; other errors don't."""
    try:
        await asyncio.sleep(INITIAL_DELAY.total_seconds())
    except asyncio.CancelledError:
        raise

    while True:
        try:
            await introspect_tick(db, interval=INTROSPECT_INTERVAL)
            await asyncio.sleep(tick_interval)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("introspect_loop iteration failed")
            await asyncio.sleep(60)


def start_scheduler(db: Database, tick_interval: float = 3600.0) -> asyncio.Task:
    """Start the introspect loop as a background task.

    `tick_interval` is how often the loop re-checks the watermark. The
    watermark itself decides whether to fire — the tick is cheap (one SQL
    query). Tests pass a tiny tick_interval to keep them fast.
    """
    return asyncio.create_task(_introspect_loop(db, tick_interval), name="introspect-scheduler")
