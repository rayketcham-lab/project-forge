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
# #98 — daily (was weekly): generation adds ~150 ideas/day, so a weekly
# fire let the pool oscillate 1.6k -> 2.8k between passes. All siphon
# passes are idempotent and reversible; daily keeps steady-state near the
# density cap instead of 7 days of drift above it.
SIPHON_INTERVAL = _interval_from_env("FORGE_SIPHON_INTERVAL_HOURS", 24.0)
FUNDABILITY_SCORE_INTERVAL = _interval_from_env("FORGE_FUNDABILITY_INTERVAL_HOURS", 24.0)
# v0.20 — Cashflow board (#96): keeps cashflow_score fresh for /cashflow.
CASHFLOW_SCORE_INTERVAL = _interval_from_env("FORGE_CASHFLOW_INTERVAL_HOURS", 24.0)
# #100 — Forge Mechanic: how often the (disarmed-by-default) autonomous
# self-improvement cadence considers a Think Tank item.
MECHANIC_INTERVAL = _interval_from_env("FORGE_MECHANIC_INTERVAL_HOURS", 24.0)
AUTO_PROMOTE_INTERVAL = _interval_from_env("FORGE_AUTO_PROMOTE_INTERVAL_HOURS", 168.0)
ISSUE_SYNC_INTERVAL = _interval_from_env("FORGE_ISSUE_SYNC_INTERVAL_HOURS", 1.0)
# v0.16 — grounded competitive-displacement generation for the Sniper board.
SNIPE_INTERVAL = _interval_from_env("FORGE_SNIPE_INTERVAL_HOURS", 6.0)
# v0.17 — Scoreboard: capture realized outcome signals for the engine's bets.
SCOREBOARD_INTERVAL = _interval_from_env("FORGE_SCOREBOARD_INTERVAL_HOURS", 24.0)
# v0.17 — Cartographer: weekly corpus white-space/saturation memo.
CARTOGRAPHER_INTERVAL = _interval_from_env("FORGE_CARTOGRAPHER_INTERVAL_HOURS", 168.0)
# v0.17 — Pulse: event-driven generation seeded by live HN/GitHub signal.
PULSE_INTERVAL = _interval_from_env("FORGE_PULSE_INTERVAL_HOURS", 3.0)
# v0.18 — Missions (#84): operator-directed generation, round-robin over
# active missions.
MISSION_INTERVAL = _interval_from_env("FORGE_MISSION_INTERVAL_HOURS", 4.0)
# v0.23 — PKI board: hourly grounded probe, one gated target per fire. Runs
# hourly not because it produces hourly, but because the sources it watches
# (IETF drafts, implementation trackers) change on that timescale; most
# fires deliberately store nothing.
PKI_INTERVAL = _interval_from_env("FORGE_PKI_INTERVAL_HOURS", 1.0)
# Keeps pki_urgency_score fresh for anything that reached the board by
# churn or predates the axis.
PKI_SCORE_INTERVAL = _interval_from_env("FORGE_PKI_SCORE_INTERVAL_HOURS", 24.0)


def _seconds_from_env(var: str, default_seconds: float) -> timedelta:
    """Same warn-and-fallback pattern as `_interval_from_env`, but the
    unit is seconds. Fix #77 — a bare `float(os.environ.get(...))`
    raised ValueError at import time on any non-numeric value
    (e.g. "fast", "1m"), crashing uvicorn before FastAPI could register
    routes."""
    raw = os.environ.get(var)
    if raw is None:
        return timedelta(seconds=default_seconds)
    try:
        return timedelta(seconds=float(raw))
    except ValueError:
        logger.warning("Invalid %s=%r; using default %ss", var, raw, default_seconds)
        return timedelta(seconds=default_seconds)


INITIAL_DELAY = _seconds_from_env("FORGE_SCHED_INITIAL_DELAY_SEC", 60.0)


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
    tasks = [asyncio.create_task(_cadence_loop(db, c), name=f"sched-{c.name}") for c in cadences]
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


async def seconds_until_next_snipe(db: Database, interval: timedelta) -> float:
    """Delay until the next snipe-generation fire.

    Watermark = MAX(generated_at) over snipe-mode ideas. Survives uvicorn
    reloads (which restart the supervisor) so we don't spam the LLM + the
    external intel APIs every reload.
    """
    cursor = await db.db.execute("SELECT MAX(generated_at) FROM ideas WHERE generation_mode = 'snipe'")
    row = await cursor.fetchone()
    return _delay_from_watermark(row[0] if row else None, interval)


async def seconds_until_next_pulse(db: Database, interval: timedelta) -> float:
    """Delay until the next pulse fire. Watermark = MAX(generated_at) over
    pulse-mode ideas only — NOT the global watermark, which the hourly expand
    cadence keeps perpetually fresh (that left the Pulse avenue effectively dead)."""
    cursor = await db.db.execute("SELECT MAX(generated_at) FROM ideas WHERE generation_mode = 'pulse'")
    row = await cursor.fetchone()
    return _delay_from_watermark(row[0] if row else None, interval)


async def seconds_until_next_pki(db: Database, interval: timedelta) -> float:
    """Delay until the next PKI probe.

    Watermark = MAX(pki_probes.probed_at), NOT MAX(ideas.generated_at) over
    PKI ideas. The probe is designed to store nothing most hours, so an
    idea-based watermark would leave the cadence permanently overdue and
    re-firing every tick — burning a generation call each pass. The probe
    log advances on every attempt, admitted or rejected, which is exactly
    the semantics this schedule needs."""
    cursor = await db.db.execute("SELECT MAX(probed_at) FROM pki_probes")
    row = await cursor.fetchone()
    return _delay_from_watermark(row[0] if row else None, interval)


async def seconds_until_next_mission(db: Database, interval: timedelta) -> float:
    """Delay until the next mission fire.

    Watermark = MAX(missions.last_generated_at) over ACTIVE missions —
    advanced on every generation attempt (saved or dedup-rejected), so
    uvicorn reloads and rejection streaks don't re-fire it. With zero
    active missions there is nothing to do: report a full interval so the
    tick skips quietly instead of firing a no-op runner every pass.
    """
    cursor = await db.db.execute("SELECT COUNT(*), MAX(last_generated_at) FROM missions WHERE status = 'active'")
    row = await cursor.fetchone()
    active_count = row[0] if row else 0
    if not active_count:
        return interval.total_seconds()
    return _delay_from_watermark(row[1], interval)


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
    from project_forge.cron.introspect_runner import (
        _recent_commit_subjects,
        run_introspect_cycle,
    )
    from project_forge.engine.thinktank_reconcile import reconcile_thinktank

    # Self-clean before generating: mark suggestions whose work already
    # shipped so they leave the board and the avoid-duplicates list (#91).
    subjects = await asyncio.to_thread(_recent_commit_subjects)
    report = await reconcile_thinktank(db, subjects)
    if report["implemented"]:
        logger.info("Think Tank reconciler marked %d items implemented", len(report["implemented"]))

    generator = _resolve_generator()
    if generator is None:
        logger.info("Introspect using static fallback (no LLM backend)")
    else:
        logger.info("Introspect using backend: %s", type(generator).__name__)
    await run_introspect_cycle(db, generator)


async def _fire_siphon(db: Database) -> None:
    """Run one live siphon pass so the pool dedups itself weekly (#94).

    The pool regrew to 3,294 active ideas before a manual trim archived
    731 near-dupes; siphon_all is idempotent and archives restorably
    (archived_reason + archived_at), so a live weekly pass is safe.
    """
    from project_forge.engine.siphon import siphon_all

    report = await siphon_all(db, dry_run=False)
    applied = sum(sub.get("applied_count", 0) for sub in report.values() if isinstance(sub, dict))
    logger.info("Siphon cadence archived %d duplicate ideas", applied)


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


def _self_improve_armed() -> bool:
    """The autonomous self-improve loop writes code and opens PRs, so it is
    DISARMED by default (#99). It fires only when FORGE_SELF_IMPROVE_ENABLED
    is truthy — the deliberate arm step. Shipping the capability must never
    start an unattended code-modifying loop on the next uvicorn reload."""
    import os

    return os.environ.get("FORGE_SELF_IMPROVE_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


async def _fire_self_improve(db: Database) -> None:
    """Run one self-improve cycle IF armed (#99). No-ops when disarmed.

    The runner is GitHub-driven (ci-queue label) so it ignores `db`.
    Errors are swallowed at this layer so the supervisor's child-loop
    `except Exception` never trips for a transient GitHub outage.
    """
    if not _self_improve_armed():
        logger.debug("self-improve cadence disarmed (FORGE_SELF_IMPROVE_ENABLED unset)")
        return

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
    from project_forge.engine.llm_backend import resolve_cheap_backend

    # v0.21 (#97): keyless deployments burst — the heuristic is instant, so
    # the small batch only exists to bound the LLM tie-break time. With a
    # backend present the writer-lock discipline above still applies.
    limit = 5 if resolve_cheap_backend() is not None else 200
    result = await score_pending_ideas(db, limit=limit)
    logger.info("Fundability cycle scored %d ideas", result.get("scored", 0))


def _mechanic_armed() -> bool:
    """The mechanic writes code and opens PRs autonomously, so its cadence is
    DISARMED by default (#100) — it fires only when FORGE_MECHANIC_ENABLED is
    truthy. Each fire launches ONE isolated one-shot process; the operator
    still merges every PR via the review panel."""
    import os

    return os.environ.get("FORGE_MECHANIC_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


async def _fire_mechanic(db: Database) -> None:
    """Launch one mechanic cycle IF armed. Spawns a detached one-shot process
    (never blocks the scheduler on the agent) and returns."""
    if not _mechanic_armed():
        logger.debug("mechanic cadence disarmed (FORGE_MECHANIC_ENABLED unset)")
        return
    from project_forge.cron.mechanic_runner import spawn_mechanic_run

    try:
        spawn_mechanic_run()
        logger.info("Mechanic cycle launched (detached one-shot)")
    except Exception:
        logger.exception("failed to launch mechanic cycle")


async def _fire_cashflow_score(db: Database) -> None:
    """Score recent unscored cashflow-board ideas for time-to-first-dollar.

    Batch deliberately small (5) for the same SQLite writer-lock reason as
    `_fire_fundability_score` — catch up over multiple ticks instead of
    holding the writer for minutes.
    """
    from project_forge.engine.cashflow import score_pending_cashflow
    from project_forge.engine.llm_backend import resolve_cheap_backend

    # v0.21 (#97): same adaptive batch as the fundability runner.
    limit = 5 if resolve_cheap_backend() is not None else 200
    result = await score_pending_cashflow(db, limit=limit)
    logger.info("Cashflow cycle scored %d ideas", result.get("scored", 0))


async def _fire_pki_score(db: Database) -> None:
    """Back-fill pki_urgency_score for PKI-board ideas that predate the axis
    (or arrived by churn). Same small adaptive batch as the other scorers so
    the SQLite writer isn't held for minutes."""
    from project_forge.engine.llm_backend import resolve_cheap_backend
    from project_forge.engine.pki import score_pending_pki_urgency

    limit = 5 if resolve_cheap_backend() is not None else 200
    result = await score_pending_pki_urgency(db, limit=limit)
    logger.info("PKI scoring cycle scored %d ideas", result.get("scored", 0))


async def _record_pki_drop(
    db: Database,
    gap: dict,
    *,
    anchor: str | None,
    reason: str,
    score: float | None = None,
) -> None:
    """Log a probe that stored nothing. Every rejection stage funnels through
    here so a quiet hour always says which stage did the dropping."""
    await db.record_pki_probe(
        gap_summary=gap.get("title"),
        anchor=anchor,
        admitted=False,
        reason=reason,
        urgency_score=score,
    )


async def _fire_pki(db: Database) -> None:
    """The hourly PKI probe: ONE target, and it is allowed to come back empty.

    Deliberately unlike every other generation cadence in this codebase.
    The others always try to produce something; this one runs a grounded
    probe, picks the single highest-leverage gap it found, works that one
    gap hard, and then applies an admission gate that most attempts fail.

      probe -> pick ONE gap -> generate draft -> red-team panel -> gate ->
      prior-art check -> store or DROP

    Selectivity alone was not enough. A gate that admits one draft in ten
    still admits a single LLM pass: a paragraph that cites an RFC and that a
    CA engineer would not act on, because nobody tried to break it. So two
    stages sit either side of the gate. `deepen()` breaks the draft three
    ways and rewrites it around what survived, and can kill it outright.
    `check_prior_art()` then asks the question that actually kills
    certificate tooling — somebody shipped this in 2017 and it has four
    thousand stars.

    There is no fallback generation. If the probe finds nothing, the panel
    kills the draft, the score lands below the threshold, or a maintained
    tool already does the job, this cadence stores NOTHING and logs why.
    That is the intended behavior: the board is a short list of things that
    matter, not an hourly deposit. Every attempt is recorded in `pki_probes`
    so the quiet hours are auditable and so the schedule has a watermark
    that advances even when nothing is stored.

    Touches no GitHub state — governance rule for autonomous cadences.
    """
    from project_forge.engine.dedup import filter_and_save
    from project_forge.engine.llm_generator import generate_idea_llm
    from project_forge.engine.pki import admits, extract_anchor, score_pki_urgency
    from project_forge.engine.pki_depth import deepen
    from project_forge.engine.pki_prior_art import check_prior_art
    from project_forge.feeds.pki_probe import fetch_pki_gaps, gap_to_seed, pick_top_gap
    from project_forge.models import IdeaCategory

    gap: dict | None = None
    try:
        # 1. Probe the real sources. Never raises; [] is a normal outcome.
        recent = await db.list_pki_probes(limit=200)
        seen_urls = {p["anchor"] for p in recent if p.get("anchor")}
        gaps = fetch_pki_gaps()

        # 2. Exactly one gap gets worked this hour.
        gap = pick_top_gap(gaps, seen_urls=seen_urls)
        if gap is None:
            await db.record_pki_probe(
                gap_summary=None,
                anchor=None,
                admitted=False,
                reason="no new PKI gap surfaced from any source",
            )
            logger.info("PKI probe: no new gap surfaced — storing nothing")
            return

        # 3. Work it. The seed demands the anchor, mechanism, tooling gap,
        #    blast radius, and a validation plan.
        try:
            category = IdeaCategory(gap.get("category") or "cert-lifecycle")
        except ValueError:
            category = IdeaCategory.CERT_LIFECYCLE

        result = await generate_idea_llm(db, category, mode="novel", seed=gap_to_seed(gap))
        if result is None:
            await db.record_pki_probe(
                gap_summary=gap.get("title"),
                anchor=gap.get("url"),
                admitted=False,
                reason="generator returned no parseable idea",
            )
            logger.info("PKI probe: generator produced nothing for %r", gap.get("title"))
            return

        idea = result.idea
        # Tag the cadence's own watermark dimension and pin the anchor. The
        # probe's source URL is the fallback anchor when the model didn't
        # cite something more specific itself.
        idea.generation_mode = "pki"
        idea.pki_anchor = extract_anchor(idea) or (gap.get("url") or None)

        # 4. Has somebody already shipped this? Three cheap HTTP searches,
        #    and they run FIRST: a duplicate should never cost five LLM
        #    calls, and the near-misses are the evidence the red team's
        #    novelty lens needs to adjudicate instead of guess. Fails open by
        #    design — a rate limit must never masquerade as "already exists".
        prior = await check_prior_art(idea)
        if prior.exists:
            await _record_pki_drop(db, gap, anchor=idea.pki_anchor, reason=prior.reason)
            logger.info("PKI probe: prior art killed %r — %s", idea.name, prior.reason)
            return

        # 5. The red team. Three adversarial lenses plus one rewrite around
        #    what survived. A fatal "already solved" hit, or two landed hits
        #    anywhere, kills the draft — rewording cannot save an idea that
        #    is wrong in two dimensions. Keyless this is a free no-op.
        depth = await deepen(idea, prior_art=prior.matches)
        if not depth.survived:
            await _record_pki_drop(
                db,
                gap,
                anchor=idea.pki_anchor,
                reason=f"red-team panel killed it: {depth.strongest or 'no surviving rationale'}",
            )
            logger.info("PKI probe: panel killed %r after %d passes", idea.name, depth.passes)
            return

        # Score the REVISED text, not the draft the panel tore up. The
        # objection is only published when the rewrite actually landed —
        # otherwise the card would show a counterargument the text below it
        # never answers.
        idea = depth.idea
        idea.pki_objection = depth.strongest

        # 6. The gate. Anchor + urgency threshold + right board.
        score = await score_pki_urgency(idea)
        idea.pki_urgency_score = score
        ok, reason = admits(idea, score)
        if not ok:
            await _record_pki_drop(
                db,
                gap,
                anchor=idea.pki_anchor or gap.get("url"),
                reason=reason,
                score=score,
            )
            logger.info("PKI probe: rejected %r — %s", idea.name, reason)
            return

        # 7. Survived everything; dedup still gets the last word. The closest
        #    prior art rides along on the admitted probe: it is the only
        #    calibration data available for tuning the match threshold, and
        #    it was being thrown away every hour.
        _saved, stored, dedup_reason = await filter_and_save(idea, db)
        await db.record_pki_probe(
            gap_summary=gap.get("title"),
            anchor=idea.pki_anchor,
            admitted=bool(stored),
            reason=f"admitted ({prior.reason})" if stored else f"dedup rejected: {dedup_reason}",
            idea_id=idea.id if stored else None,
            urgency_score=score,
        )
        logger.info(
            "PKI probe: %s %r (urgency=%.2f, anchor=%s)",
            "ADMITTED" if stored else "dedup-rejected",
            idea.name,
            score,
            idea.pki_anchor,
        )
    except Exception as exc:
        logger.exception("PKI probe cycle failed")
        # The watermark is MAX(pki_probes.probed_at), so a crash that records
        # nothing leaves the cadence permanently overdue: it re-fires every
        # tick, and because `seen_urls` also comes from the probe log it picks
        # the SAME gap each time. A deterministic failure would become an
        # unbounded LLM burn loop on one gap. Recording the crash advances the
        # watermark and retires the gap.
        try:
            await db.record_pki_probe(
                gap_summary=(gap or {}).get("title"),
                anchor=(gap or {}).get("url"),
                admitted=False,
                reason=f"probe crashed: {type(exc).__name__}: {exc}"[:400],
            )
        except Exception:
            logger.exception("PKI probe: could not record the crash either")


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


async def _fire_issue_sync(db: Database) -> None:
    """Pull live GH issue state for every approved+promoted idea and update
    the DB so the dashboard never shows '✓ promoted' on a closed issue."""
    from project_forge.cron.issue_sync_runner import run_issue_sync_cycle

    result = await run_issue_sync_cycle(db)
    logger.info(
        "Issue-sync cycle: checked=%d updated=%d",
        result.get("checked", 0),
        result.get("updated", 0),
    )


async def _fire_snipe(db: Database) -> None:
    """Generate a small batch of grounded competitive-displacement snipes
    across the Sniper hunting grounds; score + dedup-save each.

    Batch is tiny (2) — each snipe makes a couple of external intel calls
    plus an LLM call, so we catch up over ticks rather than hammering.
    Touches no GitHub state, so re-firing on a uvicorn reload is benign.
    """
    import random as _random

    from project_forge.engine.dedup import filter_and_save
    from project_forge.engine.llm_generator import generate_snipe_llm
    from project_forge.engine.snipe import score_snipe
    from project_forge.models import SNIPER_CATEGORIES

    made = 0
    cats = list(SNIPER_CATEGORIES)
    _random.shuffle(cats)
    for category in cats[:2]:
        try:
            result = await generate_snipe_llm(db, category)
            if result is None:
                continue
            result.idea.snipe_score = await score_snipe(result.idea)
            _saved, ok, _reason = await filter_and_save(result.idea, db)
            if ok:
                made += 1
        except Exception:
            logger.exception("snipe generation failed for %s", category.value)
    logger.info("Snipe cycle produced %d ideas", made)


async def _fire_scoreboard(db: Database) -> None:
    """Capture realized outcome signals for the engine's Sniper bets — the
    autonomous LEARN loop. Grounded on the top OSS-challenger star count for
    each named incumbent (cached per incumbent). Best-effort; never raises."""
    from project_forge.engine.llm_generator import _incumbent_cache
    from project_forge.engine.scoreboard import capture_outcome_signals
    from project_forge.feeds.market_intel import fetch_incumbent_intel

    def _gh_stars(incumbent: str) -> int | None:
        bundle = fetch_incumbent_intel(incumbent, cache=_incumbent_cache(incumbent))
        challengers = bundle.get("oss_challengers") or []
        return max((c.get("stars", 0) for c in challengers), default=None)

    result = await capture_outcome_signals(db, gh_stars=_gh_stars)
    logger.info("Scoreboard cycle captured %d outcome signals", result.get("captured", 0))

    # Gated learning: only when the operator opts in. Default off → the
    # scorers see an empty nudge cache and behave exactly as before.
    if os.environ.get("FORGE_SCOREBOARD_AUTOTUNE", "").lower() in ("1", "true", "yes", "on"):
        from project_forge.engine.scoreboard import apply_autotune

        tuned = await apply_autotune(db)
        logger.info("Scoreboard auto-tune applied %d learned nudges", tuned.get("applied", 0))


async def _fire_cartographer(db: Database) -> None:
    """Build the corpus atlas (white space + saturation) and log the memo
    headline. Read-only aggregate over the corpus — no external calls."""
    from project_forge.engine.cartographer import build_atlas

    atlas = await build_atlas(db)
    logger.info(
        "Cartographer atlas: white_space=%d saturation=%d next_bet=%r",
        len(atlas.get("white_space", [])),
        len(atlas.get("saturation", [])),
        atlas.get("recommended_next_bet"),
    )


async def _fire_pulse(db: Database) -> None:
    """React to the world: pull the hottest live HN/GitHub signal and generate
    one fresh idea anchored to it. Best-effort; degrades to a normal churn if
    no signal returns. Touches no GitHub state."""
    import random as _random

    from project_forge.engine.dedup import filter_and_save
    from project_forge.engine.fundability import score_fundability
    from project_forge.engine.llm_generator import generate_idea_llm
    from project_forge.feeds.pulse import (
        fetch_pulse_signals,
        pick_hot_signal,
        signal_to_seed,
    )
    from project_forge.models import MONEY_CATEGORIES

    try:
        hot = pick_hot_signal(fetch_pulse_signals())
        seed = signal_to_seed(hot) if hot else None
        category = _random.choice(MONEY_CATEGORIES)
        result = await generate_idea_llm(db, category, mode="novel", seed=seed)
        if result is None:
            logger.info("Pulse cycle: no idea produced")
            return
        # Tag as 'pulse' so seconds_until_next_pulse can track this cadence's own
        # watermark. Without it the cadence keyed off the global expand watermark,
        # which the hourly expand cadence kept perpetually fresh -> pulse ~never fired.
        result.idea.generation_mode = "pulse"
        result.idea.fundability_score = await score_fundability(result.idea)
        _saved, ok, _reason = await filter_and_save(result.idea, db)
        logger.info("Pulse cycle: %s (seed=%s)", "saved" if ok else "rejected", bool(seed))
    except Exception:
        logger.exception("pulse cycle failed")


async def _fire_mission(db: Database) -> None:
    """One operator-directed generation: pick the active mission that has
    waited longest, anchor a generation to its brief + grounding URLs.
    Touches no GitHub state, so a stray re-fire is benign."""
    from project_forge.engine import mission as mission_engine

    picked = await db.pick_next_mission()
    if picked is None:
        logger.info("Mission cycle: no active missions")
        return
    result = await mission_engine.generate_mission_idea(db, picked)
    if result is None:
        logger.info("Mission cycle: no idea produced for %s", picked.title)
        return
    logger.info(
        "Mission cycle: %s for mission %r (%s)",
        "saved" if result.saved else f"rejected ({result.reason})",
        picked.title,
        result.idea.name,
    )


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
        (
            "nvd",
            lambda: nvd.fetch(
                cache=FeedCache(base / "nvd.json", ttl=_td(hours=12)),
                days=7,
            ),
        ),
        (
            "arxiv",
            lambda: arxiv.fetch(
                cache=FeedCache(base / "arxiv.json", ttl=_td(hours=48)),
                category="cs.CR",
                max_results=25,
            ),
        ),
        (
            "ietf",
            lambda: ietf.fetch(
                cache=FeedCache(base / "ietf.json", ttl=_td(hours=24)),
            ),
        ),
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
            # #94 — weekly pool self-cleaning. Pure clock: siphon_all is
            # idempotent and near-free when the pool is already clean.
            name="siphon",
            interval=SIPHON_INTERVAL,
            runner=_fire_siphon,
            delay_query=None,
            tick_interval=SIPHON_INTERVAL.total_seconds(),
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
        Cadence(
            # v0.20 — Cashflow board (#96): keeps cashflow_score fresh so
            # /cashflow ranks keyless auto-generated ideas without operator
            # action. Pure clock; the runner skips already-scored ideas.
            name="cashflow_score",
            interval=CASHFLOW_SCORE_INTERVAL,
            runner=_fire_cashflow_score,
            delay_query=None,
            tick_interval=CASHFLOW_SCORE_INTERVAL.total_seconds(),
            initial_delay=initial,
        ),
        Cadence(
            # #100 — Forge Mechanic: autonomous self-improvement. DISARMED by
            # default (_fire_mechanic no-ops unless FORGE_MECHANIC_ENABLED);
            # each fire launches one isolated one-shot process that opens a PR
            # for the operator to merge. Pure clock.
            name="mechanic",
            interval=MECHANIC_INTERVAL,
            runner=_fire_mechanic,
            delay_query=None,
            tick_interval=MECHANIC_INTERVAL.total_seconds(),
            initial_delay=initial,
        ),
        Cadence(
            # v0.14c — pull live GitHub issue state for promoted ideas and
            # update DB status so the dashboard never shows '✓ promoted'
            # on a closed issue. Hourly is cheap (≤ N gh API calls where
            # N = active promotions, usually < 10).
            name="issue_sync",
            interval=ISSUE_SYNC_INTERVAL,
            runner=_fire_issue_sync,
            delay_query=None,
            tick_interval=ISSUE_SYNC_INTERVAL.total_seconds(),
            initial_delay=initial,
        ),
        Cadence(
            # v0.16 — grounded competitive-displacement generation for the
            # Sniper board. Watermark-gated so uvicorn reloads don't re-fire
            # it; touches no GitHub state so a stray fire is harmless.
            name="snipe",
            interval=SNIPE_INTERVAL,
            runner=_fire_snipe,
            delay_query=seconds_until_next_snipe,
            tick_interval=1800.0,  # re-check every 30min; fires per watermark
            initial_delay=initial,
        ),
        Cadence(
            # v0.17 — Scoreboard: capture realized outcome signals for the
            # engine's bets (read-only external fetches, no GH state).
            name="scoreboard",
            interval=SCOREBOARD_INTERVAL,
            runner=_fire_scoreboard,
            delay_query=None,
            tick_interval=SCOREBOARD_INTERVAL.total_seconds(),
            initial_delay=initial,
        ),
        Cadence(
            # v0.17 — Cartographer: weekly corpus strategy memo (read-only).
            name="cartographer",
            interval=CARTOGRAPHER_INTERVAL,
            runner=_fire_cartographer,
            delay_query=None,
            tick_interval=CARTOGRAPHER_INTERVAL.total_seconds(),
            initial_delay=initial,
        ),
        Cadence(
            # v0.17 — Pulse: event-driven generation seeded by live signal.
            # Watermark-gated so reloads don't re-fire; no GH state touched.
            name="pulse",
            interval=PULSE_INTERVAL,
            runner=_fire_pulse,
            delay_query=seconds_until_next_pulse,
            tick_interval=1800.0,
            initial_delay=initial,
        ),
        Cadence(
            # v0.18 — Missions (#84): operator-directed generation, round-
            # robin over active missions. Watermark = missions.last_generated_at
            # (advanced on every attempt) so reloads don't re-fire; skips
            # quietly when no missions are active. No GH state touched.
            name="mission",
            interval=MISSION_INTERVAL,
            runner=_fire_mission,
            delay_query=seconds_until_next_mission,
            tick_interval=1800.0,
            initial_delay=initial,
        ),
        Cadence(
            # v0.23 — PKI board: hourly grounded probe, ONE gated target per
            # fire, and most fires deliberately store nothing. Watermark is
            # pki_probes.probed_at (advanced on every attempt, admitted or
            # not) — an idea-based watermark would leave it permanently
            # overdue, since storing nothing is the common case. Read-only
            # external fetches; touches no GitHub state.
            name="pki",
            interval=PKI_INTERVAL,
            runner=_fire_pki,
            delay_query=seconds_until_next_pki,
            tick_interval=900.0,  # re-check every 15min; fires per watermark
            initial_delay=initial,
        ),
        Cadence(
            # v0.23 — PKI urgency back-fill for ideas that reached the board
            # by churn or predate the axis.
            name="pki_score",
            interval=PKI_SCORE_INTERVAL,
            runner=_fire_pki_score,
            delay_query=None,
            tick_interval=PKI_SCORE_INTERVAL.total_seconds(),
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
