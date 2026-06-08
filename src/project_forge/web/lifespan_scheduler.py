"""In-process scheduler for the FastAPI lifespan.

Owns the autonomous cadences that used to live in `/etc/systemd/system/`
`project-forge-*.timer` units. Those are unreachable from the
bwrap-sandboxed runtime (no DBUS bus, no sudo, no write access to
`/etc/systemd/`), so file writes + `uvicorn --reload` is the only deploy
path.

The lifespan owns one supervisor task; the supervisor owns one child
loop per `Cadence`. A child failing once is logged and retried; a child
crashing repeatedly does not stop its siblings; cancelling the
supervisor cancels every child.

Cadences currently scheduled (see `default_cadences`):
- expand:       1h   (cross-category + super idea generation)
- review:       12h  (auto-archive sweeps over aged ideas)
- self_improve: 6h   (GitHub ci-queue → PR loop; self-skips without an API key)
- introspect:   24h  (self-improvement idea proposals)
- challenge:    168h (autonomous adversarial pass on top unchallenged ideas)
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from project_forge.storage.db import Database

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Module-level config knobs (env overridable; kept here for testability)
# --------------------------------------------------------------------------- #


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
EXPAND_INTERVAL = _interval_from_env("FORGE_EXPAND_INTERVAL_HOURS", 1.0)
REVIEW_INTERVAL = _interval_from_env("FORGE_REVIEW_INTERVAL_HOURS", 12.0)
SELF_IMPROVE_INTERVAL = _interval_from_env("FORGE_SELF_IMPROVE_INTERVAL_HOURS", 6.0)
CHALLENGE_INTERVAL = _interval_from_env("FORGE_CHALLENGE_INTERVAL_HOURS", 168.0)
VERDICT_AUDIT_INTERVAL = _interval_from_env("FORGE_VERDICT_AUDIT_INTERVAL_HOURS", 24.0)
FEED_REFRESH_INTERVAL = _interval_from_env("FORGE_FEED_REFRESH_INTERVAL_HOURS", 24.0)
FUNDABILITY_SCORE_INTERVAL = _interval_from_env("FORGE_FUNDABILITY_INTERVAL_HOURS", 24.0)
AUTO_PROMOTE_INTERVAL = _interval_from_env("FORGE_AUTO_PROMOTE_INTERVAL_HOURS", 168.0)

INITIAL_DELAY = timedelta(seconds=float(os.environ.get("FORGE_SCHED_INITIAL_DELAY_SEC", "60")))


# --------------------------------------------------------------------------- #
# Generic Cadence machinery
# --------------------------------------------------------------------------- #


DelayQuery = Callable[[Database, timedelta], Awaitable[float]]
Runner = Callable[[Database], Awaitable[None]]


@dataclass
class Cadence:
    """One scheduled task owned by the supervisor.

    - `interval`: minimum time between fires. Honoured via `delay_query`.
    - `runner`: the work; receives the shared `Database` handle.
    - `delay_query`: optional. If present, seconds until the next fire
      should happen. When > 0, the tick skips. If None, every tick fires.
    - `tick_interval`: how often the loop re-checks the watermark. Cheap
      (one SQL query). Per-cadence so high-frequency cadences (expand) can
      poll faster than low-frequency ones (challenge).
    - `initial_delay`: seconds before the loop's first iteration. Lets tests
      get sub-second runs; production uses INITIAL_DELAY (60s) so we don't
      stampede the LLM backend at boot.
    """

    name: str
    interval: timedelta
    runner: Runner
    delay_query: DelayQuery | None = None
    tick_interval: float = 3600.0
    initial_delay: float = 0.0
    metadata: dict = field(default_factory=dict)


async def cadence_tick(db: Database, cadence: Cadence) -> None:
    """One iteration of a cadence: check the watermark; fire if overdue.

    Errors are logged but never propagate — the loop has to survive them.
    """
    try:
        if cadence.delay_query is not None:
            delay = await cadence.delay_query(db, cadence.interval)
            if delay > 0:
                return
        logger.info("Scheduler firing cadence: %s", cadence.name)
        await cadence.runner(db)
    except Exception:
        logger.exception("cadence %s tick failed", cadence.name)


async def _cadence_loop(db: Database, cadence: Cadence) -> None:
    """Run a cadence forever. Cancellation propagates; errors don't."""
    try:
        await asyncio.sleep(cadence.initial_delay)
    except asyncio.CancelledError:
        raise

    while True:
        try:
            await cadence_tick(db, cadence)
            await asyncio.sleep(cadence.tick_interval)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("cadence %s loop iteration failed", cadence.name)
            await asyncio.sleep(60)


async def _supervisor(db: Database, cadences: list[Cadence]) -> None:
    """Own N cadence loops. Cancelling the supervisor cancels every child."""
    tasks = [
        asyncio.create_task(_cadence_loop(db, c), name=f"sched-{c.name}")
        for c in cadences
    ]
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        for t in tasks:
            t.cancel()
        # Children raise CancelledError on cancel or may have failed in
        # flight; gather(return_exceptions=True) drains them all cleanly.
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


# --------------------------------------------------------------------------- #
# Per-cadence delay queries and runners
# --------------------------------------------------------------------------- #


async def seconds_until_next_introspect(db: Database, interval: timedelta) -> float:
    """Delay until the next introspect fire.

    Watermark = MAX of:
      - `ideas.generated_at` where `category='self-improvement'`
      - `filtered_ideas.filtered_at` where `idea_category='self-improvement'`
    A filtered SI attempt still proves the runner fired, so a streak of
    100%-dedup days does NOT let the loop hammer the LLM backend every tick.
    """
    cursor = await db.db.execute(
        "SELECT MAX(ts) FROM ("
        "  SELECT generated_at AS ts FROM ideas WHERE category = 'self-improvement'"
        "  UNION ALL"
        "  SELECT filtered_at AS ts FROM filtered_ideas WHERE idea_category = 'self-improvement'"
        ")"
    )
    row = await cursor.fetchone()
    return _delay_from_watermark(row[0] if row else None, interval)


async def seconds_until_next_expand(db: Database, interval: timedelta) -> float:
    """Delay until the next horizontal-expand fire.

    Watermark = MAX(ideas.generated_at) across ALL categories. Any
    generated idea proves the cadence fired; we don't care which category.
    """
    cursor = await db.db.execute("SELECT MAX(generated_at) FROM ideas")
    row = await cursor.fetchone()
    return _delay_from_watermark(row[0] if row else None, interval)


async def seconds_until_next_review(db: Database, interval: timedelta) -> float:
    """Delay until the next review-cycle fire.

    Watermark = MAX(idea_reviews.reviewed_at). If no review has ever been
    recorded the runner is overdue (returns 0).
    """
    cursor = await db.db.execute("SELECT MAX(reviewed_at) FROM idea_reviews")
    row = await cursor.fetchone()
    return _delay_from_watermark(row[0] if row else None, interval)


async def seconds_until_next_challenge(db: Database, interval: timedelta) -> float:
    """Delay until the next challenge-cycle fire.

    Watermark = MAX(challenges.created_at). Includes human-initiated
    challenges — if a human just challenged something we don't need
    autonomous challenges firing in the same window.
    """
    cursor = await db.db.execute("SELECT MAX(created_at) FROM challenges")
    row = await cursor.fetchone()
    return _delay_from_watermark(row[0] if row else None, interval)


def _delay_from_watermark(last_ts: str | None, interval: timedelta) -> float:
    if not last_ts:
        return 0.0
    last = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    next_fire = last + interval
    delay = (next_fire - datetime.now(UTC)).total_seconds()
    return max(0.0, delay)


def _resolve_generator():
    """Pick the best available generator without raising.

    Returns the API generator if `ANTHROPIC_API_KEY` is set, else a
    Claude-Code-CLI backend generator if `claude` is on PATH, else None
    (the static fallback path inside each runner takes over).
    """
    from project_forge.config import settings

    api_key = settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        from project_forge.engine.generator import IdeaGenerator

        return IdeaGenerator(api_key=api_key)

    from project_forge.engine.llm_backend import resolve_backend

    backend = resolve_backend(force="claude_code")
    if backend is not None:
        from project_forge.engine.generator import LLMBackendIdeaGenerator

        return LLMBackendIdeaGenerator(backend)
    return None


async def _fire_introspect(db: Database) -> None:
    """Run one introspect cycle with the best available LLM backend.

    Replicates the cron `_run()` entrypoint without its `sys.exit(1)` so
    a failure doesn't crash uvicorn.
    """
    from project_forge.cron.introspect_runner import run_introspect_cycle

    generator = _resolve_generator()
    if generator is None:
        logger.info("Introspect using static fallback (no LLM backend)")
    else:
        logger.info("Introspect using backend: %s", type(generator).__name__)
    await run_introspect_cycle(db, generator)


async def _fire_expand(db: Database) -> None:
    """Run one horizontal-expand cycle (cross-cat + super idea)."""
    from project_forge.cron.horizontal import run_horizontal_cycle

    ideas = await run_horizontal_cycle(db)
    logger.info("Expand cycle produced %d ideas", len(ideas))


async def _fire_review(db: Database) -> None:
    """Run one review cycle (heuristic if no API key, Claude if present)."""
    from project_forge.cron.review_runner import run_review_cycle

    result = await run_review_cycle(db)
    logger.info(
        "Review cycle reviewed %d ideas (%d errors)",
        result.get("reviewed", 0),
        sum(1 for r in result.get("results", []) if r.get("status") == "error"),
    )


async def _fire_self_improve(db: Database) -> None:
    """Run one self-improve cycle. No-ops cleanly without an API key.

    The runner is GitHub-driven (ci-queue label) so it ignores `db`.
    Errors are swallowed at this layer so the supervisor's child-loop
    `except Exception` never trips for a transient GitHub outage.
    """
    from project_forge.cron.self_improve_runner import run_self_improve_cycle

    try:
        result = await run_self_improve_cycle()
        logger.info(
            "Self-improve cycle processed %d issues",
            result.get("processed", 0),
        )
    except Exception:
        logger.exception("self-improve cycle failed")


async def _fire_challenge(db: Database) -> None:
    """Run one autonomous challenge cycle."""
    from project_forge.cron.challenge_runner import run_challenge_cycle

    result = await run_challenge_cycle(db)
    logger.info(
        "Challenge cycle produced %d challenges",
        result.get("challenged", 0),
    )


async def _fire_verdict_audit(db: Database) -> None:
    """Run one verdict meta-audit cycle (checker-of-the-checker)."""
    from project_forge.cron.verdict_audit_runner import run_verdict_audit_cycle

    result = await run_verdict_audit_cycle(db)
    logger.info(
        "Verdict audit cycle: %d audited, %d divergences",
        result.get("audited", 0),
        result.get("divergences", 0),
    )


async def _fire_fundability_score(db: Database) -> None:
    """Score recent unscored ideas for monetization viability.

    Batch deliberately small (5) so each tick holds the SQLite writer for
    seconds, not a minute. With 50 unscored ideas plus a Haiku tie-break
    averaging 6s each, a batch=50 tick held the writer ~5 minutes and
    made every browser POST 500 with 'database is locked'. We just
    catch up over multiple ticks instead.
    """
    from project_forge.engine.fundability import score_pending_ideas

    result = await score_pending_ideas(db, limit=5)
    logger.info("Fundability cycle scored %d ideas", result.get("scored", 0))


async def _fire_auto_promote(db: Database) -> None:
    """Pick top-fundability money-bot idea, file a GH issue, flip to
    'approved'. The money-flipper loop."""
    from project_forge.cron.auto_promote_runner import run_auto_promote_cycle

    result = await run_auto_promote_cycle(db)
    if result.get("promoted"):
        logger.info(
            "Auto-promoted idea=%s name=%r issue=%s",
            result.get("idea_id"),
            result.get("name"),
            result.get("issue_url"),
        )
    else:
        logger.info("Auto-promote cycle: no candidate this round")


async def _fire_feed_refresh(db: Database) -> None:
    """Refresh NVD / arXiv / IETF caches that feed prompt-seed material.

    Each fetcher is wrapped in its own try/except so one feed being down
    (NVD often is, briefly) does not block the others. db is unused — the
    caches live on disk under FORGE_FEEDS_DIR (defaults to
    `<db_dir>/feeds`).
    """
    from datetime import timedelta as _td
    from pathlib import Path

    from project_forge.config import settings
    from project_forge.feeds import arxiv, ietf, nvd
    from project_forge.feeds.cache import FeedCache

    feeds_dir_env = os.environ.get("FORGE_FEEDS_DIR")
    base = Path(feeds_dir_env) if feeds_dir_env else Path(settings.db_path).parent / "feeds"
    base.mkdir(parents=True, exist_ok=True)

    fetchers = [
        ("nvd", lambda: nvd.fetch(
            cache=FeedCache(base / "nvd.json", ttl=_td(hours=12)), days=7,
        )),
        ("arxiv", lambda: arxiv.fetch(
            cache=FeedCache(base / "arxiv.json", ttl=_td(hours=48)),
            category="cs.CR", max_results=25,
        )),
        ("ietf", lambda: ietf.fetch(
            cache=FeedCache(base / "ietf.json", ttl=_td(hours=24)),
        )),
    ]
    for label, run in fetchers:
        try:
            items = run()
            # Some fetchers may be async-aware; await if so.
            if hasattr(items, "__await__"):
                items = await items
            logger.info("Feed refresh: %s → %d items", label, len(items or []))
        except Exception:
            logger.exception("feed refresh: %s failed", label)


# --------------------------------------------------------------------------- #
# Backwards-compat: original introspect_tick still imported by tests/callers
# --------------------------------------------------------------------------- #


async def introspect_tick(db: Database, interval: timedelta) -> None:
    """One iteration of the introspect loop (legacy entrypoint).

    Kept as a thin wrapper so the original test module and any external
    callers continue to work. Patches against `_fire_introspect` still
    take effect, since this wrapper resolves it from the module each call.
    """
    try:
        delay = await seconds_until_next_introspect(db, interval=interval)
        if delay > 0:
            return
        logger.info("Scheduler firing introspect (legacy entrypoint)")
        # Resolve from the module so unit tests can patch _fire_introspect.
        import project_forge.web.lifespan_scheduler as _self

        await _self._fire_introspect(db)
    except Exception:
        logger.exception("introspect_tick failed")


# --------------------------------------------------------------------------- #
# Cadence registration and lifecycle
# --------------------------------------------------------------------------- #


def default_cadences() -> list[Cadence]:
    """The five production cadences."""
    initial = INITIAL_DELAY.total_seconds()
    return [
        Cadence(
            name="expand",
            interval=EXPAND_INTERVAL,
            runner=_fire_expand,
            delay_query=seconds_until_next_expand,
            tick_interval=300.0,  # re-check every 5min; fires hourly via watermark
            initial_delay=initial,
        ),
        Cadence(
            name="review",
            interval=REVIEW_INTERVAL,
            runner=_fire_review,
            delay_query=seconds_until_next_review,
            tick_interval=1800.0,  # re-check every 30min
            initial_delay=initial,
        ),
        Cadence(
            name="self_improve",
            interval=SELF_IMPROVE_INTERVAL,
            runner=_fire_self_improve,
            # Pure clock-based: the GitHub ci-queue is the watermark.
            delay_query=None,
            tick_interval=SELF_IMPROVE_INTERVAL.total_seconds(),
            initial_delay=initial,
        ),
        Cadence(
            name="introspect",
            interval=INTROSPECT_INTERVAL,
            runner=_fire_introspect,
            delay_query=seconds_until_next_introspect,
            tick_interval=3600.0,
            initial_delay=initial,
        ),
        Cadence(
            name="challenge",
            interval=CHALLENGE_INTERVAL,
            runner=_fire_challenge,
            delay_query=seconds_until_next_challenge,
            tick_interval=3600.0,
            initial_delay=initial,
        ),
        Cadence(
            name="verdict_audit",
            interval=VERDICT_AUDIT_INTERVAL,
            runner=_fire_verdict_audit,
            # Pure clock-based: the cadence audits whatever's un-audited,
            # so there's no DB-side watermark to consult — the supervisor
            # ticks it on schedule.
            delay_query=None,
            tick_interval=VERDICT_AUDIT_INTERVAL.total_seconds(),
            initial_delay=initial,
        ),
        Cadence(
            name="feed_refresh",
            interval=FEED_REFRESH_INTERVAL,
            runner=_fire_feed_refresh,
            # Pure clock-based: feed-cache TTL is the real watermark and
            # the fetchers internally respect it; ticking daily is the
            # outer safety net.
            delay_query=None,
            tick_interval=FEED_REFRESH_INTERVAL.total_seconds(),
            initial_delay=initial,
        ),
        Cadence(
            # v0.14 — keeps fundability_score fresh for the auto-promote
            # picker. Pure clock; the runner itself skips already-scored
            # ideas so re-running is cheap.
            name="fundability_score",
            interval=FUNDABILITY_SCORE_INTERVAL,
            runner=_fire_fundability_score,
            delay_query=None,
            tick_interval=FUNDABILITY_SCORE_INTERVAL.total_seconds(),
            initial_delay=initial,
        ),
        # REMOVED v0.14b — auto_promote cadence: the user explicitly
        # rejected autonomous promotion. Every uvicorn auto-reload was
        # restarting the supervisor and re-firing the 60s-initial-delay
        # cadence, producing unintended promotions (issues #79 and #80
        # on 2026-06-08). The runner code at cron.auto_promote_runner
        # stays — `/api/promote/{idea_id}` invokes it on a human click.
        # See ROADMAP.md: anything autonomous that touches GitHub state
        # gets a human gate.
    ]


def start_scheduler(
    db: Database,
    tick_interval: float | None = None,
    cadences: list[Cadence] | None = None,
) -> asyncio.Task:
    """Start the multi-cadence supervisor as a single background task.

    - `cadences`: override the production set (tests pass synthetic ones).
    - `tick_interval`: legacy escape hatch — when set, every default
      cadence inherits this tick rate. Used by the original lifecycle
      test to keep loops fast.
    """
    cs = cadences if cadences is not None else default_cadences()
    if tick_interval is not None:
        for c in cs:
            c.tick_interval = tick_interval
    return asyncio.create_task(_supervisor(db, cs), name="scheduler-supervisor")
