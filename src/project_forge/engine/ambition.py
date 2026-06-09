"""Ambition scoring — how far does this idea push Claude / agent capability.

Distinct axis from feasibility (can we build it) and fundability (can we
sell it). The /claude-lab page sorts the corpus by ambition_score DESC
so the most boundary-pushing ideas surface to the top.

The user framing (2026-06-09): "excel Claude into the 35th century" —
favor ideas that, if they existed, would shift what agents fundamentally
*can* do. Penalize derivative "$tool for $domain" pitches.

Two-stage:

  1. Heuristic (always runs, ~free):
     - Baseline 0.20.
     - Category bonus:
         CLAUDE_SKILLS_AGENTS  +0.25
         AI_MARKETPLACE        +0.22
         AUTOMATION            +0.05 (adjacent space)
     - Frontier keyword density across description + mvp_scope.
     - Anthropic / MCP ecosystem signal in tech_stack.
     - Description depth (substance proxy).

  2. LLM tie-break (borderline only, ~$0.001/call):
     When the heuristic lands in [0.40, 0.75], ask Haiku for a finer
     score. Outside that band the heuristic is taken at face value.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from project_forge.engine.llm_backend import resolve_cheap_backend
from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database

logger = logging.getLogger(__name__)


# Words that indicate the idea is reaching beyond a templated tool into
# something that reshapes how agents and their authors operate.
_FRONTIER_KEYWORDS = re.compile(
    r"\b("
    r"mcp|model[\s-]?context[\s-]?protocol|sub[\s-]?agent|skill|skills|"
    r"agent[\s-]?graph|fanned[\s-]?out|orchestrat\w+|"
    r"attribution|provenance|registry|marketplace|reputation|trust[\s-]?score|"
    r"reproducibility|ledger|leaderboard|composable|composition|"
    r"a/b\s*test\w*|insurance|lineage|royalt\w+|rev[\s-]?share|"
    r"discovery|distribution|peer[\s-]?review|long[\s-]?tail"
    r")\b",
    re.IGNORECASE,
)

# Tech-stack tokens that imply Anthropic / MCP / agent ecosystem.
_FRONTIER_STACK = {
    "anthropic", "@anthropic-ai/sdk", "anthropic-rs",
    "mcp", "@modelcontextprotocol/sdk", "modelcontextprotocol",
    "claude", "claude-code",
}

_CATEGORY_BONUS: dict[IdeaCategory, float] = {
    IdeaCategory.CLAUDE_SKILLS_AGENTS: 0.25,
    IdeaCategory.AI_MARKETPLACE: 0.22,
    IdeaCategory.AUTOMATION: 0.05,
    IdeaCategory.SELF_IMPROVEMENT: 0.05,
}

LLM_VERIFY_LOWER = 0.40
LLM_VERIFY_UPPER = 0.75


def score_ambition_heuristic(idea: Idea) -> float:
    """Cheap, deterministic frontier-bias score in [0.0, 1.0]."""
    score = 0.20

    # Category bonus.
    score += _CATEGORY_BONUS.get(idea.category, 0.0)

    # Frontier keywords — count distinct hits, cap at 4 so a keyword-stuffed
    # pitch doesn't max out the band.
    blob = " ".join([
        idea.description or "",
        idea.mvp_scope or "",
        idea.tagline or "",
    ])
    distinct_hits = {m.group(0).lower() for m in _FRONTIER_KEYWORDS.finditer(blob)}
    score += min(len(distinct_hits), 4) * 0.05

    # Anthropic / MCP stack signal.
    tech_lower = {t.lower() for t in idea.tech_stack}
    if tech_lower & _FRONTIER_STACK:
        score += 0.10

    # Description depth proxy: substantive description (≥180 chars) earns
    # a small bump; very short pitches are penalised mildly.
    desc_len = len(idea.description or "")
    if desc_len >= 240:
        score += 0.05
    elif desc_len < 80:
        score -= 0.05

    return max(0.0, min(1.0, score))


async def _llm_refine(idea: Idea, heuristic: float) -> float:
    """Ask the cheap LLM for a finer score in the borderline band."""
    backend = resolve_cheap_backend()
    if backend is None:
        return heuristic
    prompt = (
        "Rate this project idea's *frontier ambition* on a 0.0-1.0 scale. "
        "Higher = pushes what AI agents / the Claude ecosystem can do. "
        "Lower = derivative or templated. Be conservative — most ideas "
        "are derivative. Respond with JSON only, single key 'score'.\n\n"
        f"## Idea: {idea.name}\n"
        f"**Tagline:** {idea.tagline}\n"
        f"**Description:** {idea.description}\n"
        f"**MVP:** {idea.mvp_scope}\n"
        f"**Tech:** {', '.join(idea.tech_stack)}\n\n"
        "Reply: {\"score\": 0.0-1.0}"
    )
    raw = (backend.call(prompt) or "").strip()
    if "```json" in raw:
        raw = raw.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in raw:
        raw = raw.split("```", 1)[1].split("```", 1)[0].strip()
    try:
        data: dict[str, Any] = json.loads(raw)
        s = float(data["score"])
    except Exception:
        logger.info("ambition LLM parse failed; sticking with heuristic")
        return heuristic
    return max(0.0, min(1.0, s))


async def score_ambition(idea: Idea) -> float:
    """Heuristic-first, LLM tie-break in the borderline band."""
    heuristic = score_ambition_heuristic(idea)
    if LLM_VERIFY_LOWER <= heuristic <= LLM_VERIFY_UPPER:
        return await _llm_refine(idea, heuristic)
    return heuristic


async def score_pending_ambition(db: Database, limit: int = 50) -> dict[str, Any]:
    """Bulk back-fill of unscored ideas. Idempotent — already-scored ones
    are skipped."""
    cur = await db.db.execute(
        "SELECT id FROM ideas "
        "WHERE ambition_score IS NULL "
        "AND status NOT IN ('archived', 'rejected') "
        "ORDER BY generated_at DESC LIMIT ?",
        (limit,),
    )
    rows = await cur.fetchall()
    scored = 0
    for r in rows:
        idea = await db.get_idea(r["id"])
        if idea is None:
            continue
        idea.ambition_score = await score_ambition(idea)
        await db.save_idea(idea)
        scored += 1
    return {"scored": scored, "limit": limit}
