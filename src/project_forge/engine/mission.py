"""Missions — operator-directed generation (v0.18, issue #84).

Every other generation path picks its own target: category rotation,
incumbent seeds, live pulse signals. A Mission inverts that — the human
operator points the think tank at a problem space that matters to them
(free-text brief + up to 3 grounding URLs), and generation anchors to it
via the existing `seed` hook in `generate_idea_llm`. Mission ideas ride
the same persona / mode / anti-similarity / dedup machinery as everything
else; they're just aimed.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from project_forge.models import PRODUCT_MONEY_CATEGORIES, Idea, Mission
from project_forge.storage.db import Database

logger = logging.getLogger(__name__)

# The seed block rides inside an already-long prompt (persona, mode,
# anti-similarity list, JSON schema). Cap the grounding so one fat webpage
# can't crowd the rest of the prompt out.
MAX_SEED_CHARS = 3500
_PER_URL_CHARS = 1200


@dataclass
class MissionGenerationResult:
    """One mission-anchored generation attempt. `saved` is False when the
    dedup gate rejected the idea; `reason` says why."""

    idea: Idea
    saved: bool
    reason: str | None


async def build_mission_seed(
    mission: Mission,
    fetcher: Callable[[str], Awaitable] | None = None,
) -> str:
    """Render the mission into seed text: brief first, then excerpts of each
    grounding URL. URL failures degrade silently to brief-only — a directive
    with a dead link is still a directive.

    `fetcher` defaults to the SSRF-guarded `url_ingest.fetch_url_content`;
    tests inject a fake.
    """
    if fetcher is None:
        from project_forge.engine.url_ingest import fetch_url_content

        fetcher = fetch_url_content

    parts = [
        f"OPERATOR MISSION — {mission.title}",
        "The human operator has pointed generation at this target. "
        "The idea MUST directly serve this brief — not the category in general:",
        mission.brief,
    ]
    for url in mission.urls:
        try:
            content = await fetcher(url)
        except Exception:
            logger.warning("mission %s: failed to fetch grounding URL %s", mission.id, url)
            continue
        excerpt = (content.text or "")[:_PER_URL_CHARS]
        parts.append(f"Grounding source: {content.title} ({url})\n{excerpt}")
    return "\n\n".join(parts)[:MAX_SEED_CHARS]


async def generate_mission_idea(
    db: Database,
    mission: Mission,
    *,
    fetcher: Callable[[str], Awaitable] | None = None,
) -> MissionGenerationResult | None:
    """One generation anchored to the mission. Returns None when no backend
    resolves or the response fails parsing (watermark NOT advanced, so the
    cadence retries later); on any real attempt the watermark advances even
    if dedup rejects, so rejection streaks can't hammer the backend.

    Engine collaborators are resolved through their source modules at call
    time — same pattern as `_fire_pulse` — so tests can monkeypatch them.
    """
    from project_forge.engine import dedup, fundability, llm_generator

    seed = await build_mission_seed(mission, fetcher=fetcher)
    category = mission.category or random.choice(PRODUCT_MONEY_CATEGORIES)
    result = await llm_generator.generate_idea_llm(db, category, mode="novel", seed=seed)
    if result is None:
        logger.info("mission %s: no idea produced (no backend or parse failure)", mission.id)
        return None

    idea = result.idea
    idea.generation_mode = "mission"
    idea.mission_id = mission.id
    if mission.urls:
        idea.source_url = mission.urls[0]
    idea.fundability_score = await fundability.score_fundability(idea)
    _saved, ok, reason = await dedup.filter_and_save(idea, db)
    await db.touch_mission_generated(mission.id)
    logger.info(
        "mission %s: %s — %r",
        mission.id,
        "saved" if ok else f"rejected ({reason})",
        idea.name,
    )
    return MissionGenerationResult(idea=idea, saved=ok, reason=None if ok else reason)
