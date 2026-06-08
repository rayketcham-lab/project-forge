"""Fundability scoring — how monetizable does this idea look.

Distinct from `feasibility_score` (can we build it). Drives the future
auto-promotion loop: pick the top fundability_score idea each week and
route it toward scaffold + ci-queue so the engine actually ships
something with a chance of generating revenue.

Two-stage scoring:

  1. Heuristic (always runs, ~free):
     - tech_stack hints at payments (stripe, paddle, lemonsqueezy)
     - mvp_scope/description mentions paid/subscription/SaaS/recurring
     - market_analysis names a specific buyer with budget signal
     - category is monetization-friendly
     - description hints at recurring revenue / repeat usage

  2. LLM verification (borderline only, ~$0.001/call):
     When heuristic lands in [0.35, 0.70], ask Haiku for a finer score.
     Outside that band the heuristic is taken as-is.

The borderline band is deliberately narrow so the LLM bill stays
predictable: empirically ~25% of ideas land there.
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


# Words that strongly suggest a paid product.
_PAID_KEYWORDS = re.compile(
    r"\b(subscription|saas|paid|premium|pricing|tier|monetiz\w*|"
    r"recurring|mrr|arr|revenue|invoice|billing|paywall|upsell|upgrade|"
    r"price|paid plan|paid tier)\b",
    re.IGNORECASE,
)

# Tech stack tokens that imply payment integration.
_PAYMENT_STACK = {
    "stripe", "paddle", "lemonsqueezy", "lemon-squeezy", "chargebee",
    "recurly", "shopify", "gumroad", "podia", "memberful",
}

# Buyer signals: a market_analysis that names a SPECIFIC paying audience.
_BUYER_SIGNAL = re.compile(
    r"\b(indie hacker|founder|creator|operator|owner|director|cto|cfo|"
    r"head of|manager|seller|agency|smb|enterprise|smb owner|"
    r"\$\d+|\bk/?mo|\bk/yr|\bmrr|\barr)\b",
    re.IGNORECASE,
)


_CATEGORY_BONUS: dict[IdeaCategory, float] = {
    IdeaCategory.AUTOMATION_INCOME: 0.20,
    IdeaCategory.CREATOR_TOOLS: 0.12,
    IdeaCategory.CONSUMER_APP: 0.10,
    IdeaCategory.PRODUCTIVITY: 0.10,
    IdeaCategory.MARKET_GAP: 0.08,
    IdeaCategory.SECURITY_TOOL: 0.08,
    IdeaCategory.COMPLIANCE: 0.06,
    IdeaCategory.PRIVACY: 0.05,
    # Everything else: 0 (still monetizable, just no bias).
}


# Score band that triggers the LLM second opinion.
LLM_VERIFY_LOWER = 0.35
LLM_VERIFY_UPPER = 0.70


def score_fundability_heuristic(idea: Idea) -> float:
    """Cheap, deterministic monetization score in [0.0, 1.0]."""
    score = 0.20  # baseline — every idea has some non-zero shot

    # Payment-related tech stack.
    tech_lower = {t.lower() for t in idea.tech_stack}
    if tech_lower & _PAYMENT_STACK:
        score += 0.15

    # Paid-product keywords in description + scope.
    text_blob = " ".join([
        idea.description or "",
        idea.mvp_scope or "",
        idea.tagline or "",
    ])
    if _PAID_KEYWORDS.search(text_blob):
        score += 0.15

    # Buyer signal in market analysis.
    if _BUYER_SIGNAL.search(idea.market_analysis or ""):
        score += 0.15

    # Category bonus.
    score += _CATEGORY_BONUS.get(idea.category, 0.0)

    # Description-level recurring-revenue hint.
    desc_lower = (idea.description or "").lower()
    if "recurring" in desc_lower or "subscription" in desc_lower or "monthly" in desc_lower:
        score += 0.10

    return max(0.0, min(1.0, score))


async def _llm_refine(idea: Idea, heuristic: float) -> float:
    """Ask the cheap LLM for a finer score when the heuristic is borderline.
    Falls back to the heuristic on any backend / parse failure."""
    backend = resolve_cheap_backend()
    if backend is None:
        return heuristic
    prompt = (
        "Rate this project idea's monetization viability on a 0.0-1.0 scale. "
        "Be conservative — most ideas don't ship; most that ship don't make "
        "money. Respond with JSON only, single key 'score'.\n\n"
        f"## Idea: {idea.name}\n"
        f"**Tagline:** {idea.tagline}\n"
        f"**Description:** {idea.description}\n"
        f"**Market:** {idea.market_analysis}\n"
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
        logger.info("fundability LLM parse failed; sticking with heuristic")
        return heuristic
    return max(0.0, min(1.0, s))


async def score_fundability(idea: Idea) -> float:
    """Heuristic-first, LLM tie-break in the borderline band."""
    heuristic = score_fundability_heuristic(idea)
    if LLM_VERIFY_LOWER <= heuristic <= LLM_VERIFY_UPPER:
        return await _llm_refine(idea, heuristic)
    return heuristic


# --------------------------------------------------------------------------- #
# Bulk back-fill                                                              #
# --------------------------------------------------------------------------- #


async def score_pending_ideas(db: Database, limit: int = 50) -> dict[str, Any]:
    """Score active ideas that don't yet have a fundability_score. Idempotent
    — already-scored ideas are skipped. Returns a summary report."""
    cur = await db.db.execute(
        "SELECT id FROM ideas "
        "WHERE fundability_score IS NULL "
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
        idea.fundability_score = await score_fundability(idea)
        await db.save_idea(idea)
        scored += 1
    return {"scored": scored, "limit": limit}
