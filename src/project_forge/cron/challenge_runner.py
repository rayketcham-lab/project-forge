"""Autonomous challenge runner.

The challenge feature shipped in v0.5.0 as a dashboard POST endpoint and
collected exactly 5 records (one per human click) over the next two
months. This module gives the engine its own driver: each cycle picks
the top-N highest-feasibility unchallenged "new" ideas and runs one
structured adversarial pass per idea, persisting the result alongside
human-initiated challenges.

Why a separate module instead of importing the dashboard handler:
- The handler lives in `web.routes` which transitively imports a lot of
  request-scoped middleware. The cron path should not pull that in.
- The handler hard-codes a heuristic fallback that says "add an API key
  for deeper review" — fine for a dashboard but misleading on a cron log.
- Backend selection in cron prefers Claude Code CLI when no API key is
  present (the user runs Pro Max, not a raw API key).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from project_forge.engine.llm_backend import resolve_backend
from project_forge.models import Challenge, Idea
from project_forge.storage.db import Database

logger = logging.getLogger(__name__)


_DEFAULT_QUESTION = (
    "Stress-test this idea. What is the strongest reason a senior reviewer "
    "would kill it? Identify the single weakest assumption, the most likely "
    "failure mode in the first 90 days of deployment, and the cheapest "
    "experiment that would falsify the premise."
)


async def pick_ideas_to_challenge(db: Database, limit: int = 5) -> list[Idea]:
    """Top-N highest-scoring "new" ideas that have no challenge row yet.

    Ordered by feasibility_score DESC. Status filter avoids re-challenging
    ideas that have already been reviewed, approved, archived, etc.
    """
    cursor = await db.db.execute(
        """
        SELECT i.id
        FROM ideas i
        LEFT JOIN challenges c ON c.idea_id = i.id
        WHERE i.status = 'new'
          AND c.id IS NULL
        ORDER BY i.feasibility_score DESC, i.generated_at DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = await cursor.fetchall()
    ideas: list[Idea] = []
    for row in rows:
        idea = await db.get_idea(row[0])
        if idea is not None:
            ideas.append(idea)
    return ideas


async def _challenge_idea(
    idea: Idea,
    question: str,
    *,
    challenge_type: str = "freeform",
    focus_area: str = "all",
    tone: str = "skeptical",
) -> dict[str, Any]:
    """Run a single challenge against an idea via the resolved LLM backend.

    Mirrors the dashboard's prompt structure so autonomous and manual
    challenges produce comparable shapes. Falls back to a no-op response
    (verdict=no_change, empty changes) when no backend is reachable —
    that's better than piling up error rows in the DB.
    """
    backend = resolve_backend()
    if backend is None:
        logger.info("No LLM backend available; skipping challenge for %s", idea.id)
        return {
            "response": ("No LLM backend reachable at challenge time — skipped (autonomous challenge runner)."),
            "verdict": "no_change",
            "confidence": 0.0,
            "changes": [],
        }

    prompt = (
        "You are a senior technical reviewer. Respond ONLY with valid JSON.\n\n"
        "You are reviewing a project idea proposal.\n\n"
        f"## Idea: {idea.name}\n"
        f"**Tagline:** {idea.tagline}\n"
        f"**Description:** {idea.description}\n"
        f"**Market Analysis:** {idea.market_analysis}\n"
        f"**MVP Scope:** {idea.mvp_scope}\n"
        f"**Tech Stack:** {', '.join(idea.tech_stack)}\n"
        f"**Feasibility Score:** {idea.feasibility_score}\n\n"
        f"## Challenge\n**Type:** {challenge_type}\n**Focus:** {focus_area}\n"
        f"**Tone:** {tone}\n\n"
        f"**Question:**\n{question}\n\n"
        "Respond with JSON only:\n"
        "{\n"
        '  "response": "Your detailed answer",\n'
        '  "verdict": "strengthen|pivot|narrow|expand|kill|no_change",\n'
        '  "confidence": 0.0 to 1.0,\n'
        '  "changes": []\n'
        "}\n"
    )

    raw = (backend.call(prompt) or "").strip()
    if "```json" in raw:
        raw = raw.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in raw:
        raw = raw.split("```", 1)[1].split("```", 1)[0].strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {"response": raw or "(empty backend response)", "changes": []}

    return {
        "response": data.get("response", ""),
        "verdict": data.get("verdict", "no_change"),
        "confidence": float(data.get("confidence", 0.5)),
        "changes": data.get("changes", []) or [],
    }


async def run_challenge_cycle(db: Database, limit: int = 5) -> dict[str, Any]:
    """One autonomous challenge cycle.

    Picks N candidates, challenges each, persists successful results.
    Per-idea failures are captured in the results list and do not block
    remaining picks.
    """
    picks = await pick_ideas_to_challenge(db, limit=limit)
    if not picks:
        logger.info("No unchallenged 'new' ideas; nothing to do.")
        return {"challenged": 0, "results": []}

    results: list[dict[str, Any]] = []
    challenged = 0
    for idea in picks:
        try:
            outcome = await _challenge_idea(
                idea,
                _DEFAULT_QUESTION,
                challenge_type="freeform",
                focus_area="all",
                tone="skeptical",
            )
            challenge = Challenge(
                idea_id=idea.id,
                question=_DEFAULT_QUESTION,
                challenge_type="freeform",
                focus_area="all",
                tone="skeptical",
                response=outcome["response"],
                verdict=outcome["verdict"],
                confidence=outcome["confidence"],
                changes=outcome["changes"],
            )
            await db.save_challenge(challenge)
            challenged += 1
            results.append(
                {
                    "idea_id": idea.id,
                    "name": idea.name,
                    "status": "ok",
                    "verdict": outcome["verdict"],
                    "confidence": outcome["confidence"],
                }
            )
            logger.info(
                "Challenged idea %s (%s) → verdict=%s conf=%.2f",
                idea.id,
                idea.name,
                outcome["verdict"],
                outcome["confidence"],
            )
        except Exception as exc:
            logger.exception("Challenge for idea %s failed", idea.id)
            results.append(
                {
                    "idea_id": idea.id,
                    "name": idea.name,
                    "status": "error",
                    "detail": str(exc),
                }
            )

    return {"challenged": challenged, "results": results}
