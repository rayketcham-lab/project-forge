"""Cashflow scoring — how fast does this idea turn into actual dollars.

The folding-cash axis (#96). Distinct from `fundability_score` (can we
sell it as a product — biased toward recurring SaaS) and from
`feasibility_score` (can we build it). cashflow_score asks: how soon is
the first invoice, and how little capital does it take to get there?
Drives the /cashflow board ranking.

Two-stage scoring, same shape as fundability:

  1. Heuristic (always runs, ~free):
     - fast time-to-first-dollar signals (this week, presell, deposit)
     - low-capital signals (no inventory, no ad spend, digital delivery)
     - built-in-demand marketplaces (Etsy, Gumroad, Upwork, eBay, ...)
     - direct payment mechanics (invoice, deposit, flat fee, per lead)
     - venture-shaped penalties (raise, runway, network effects)
     - category is a folding-cash shape (productized services, flips)

  2. LLM verification (borderline only, ~$0.001/call):
     When the heuristic lands in [0.35, 0.70], ask the cheap backend for
     a finer score. Outside the band the heuristic is taken as-is, and
     with no backend configured the heuristic always stands — the axis
     works fully keyless, like everything else in the engine.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from project_forge.engine.llm_backend import resolve_cheap_backend
from project_forge.models import CASHFLOW_CATEGORIES, Idea, IdeaCategory
from project_forge.storage.db import Database

logger = logging.getLogger(__name__)


# Signals that the first dollar arrives in days, not quarters.
_FAST_MONEY = re.compile(
    r"\b(48[- ]hours?|72[- ]hours?|same[- ]day|this week|within days|in days|"
    r"first (?:sale|client|dollar|paying customer)|weekend|day one|"
    r"pre-?sell\w*|presell\w*|pre-?order\w*|deliver(?:ed|able|s)? in \d+ days?)\b",
    re.IGNORECASE,
)

# Signals that starting costs are near zero.
_LOW_CAPITAL = re.compile(
    r"\b(no inventory|zero inventory|no upfront|capital[- ]light|low[- ]capital|"
    r"print[- ]on[- ]demand|digital delivery|build once|no ad spend|"
    r"free to start|drop-?ship\w*)\b",
    re.IGNORECASE,
)

# Marketplaces with built-in buyers — distribution you don't have to build.
_BUILTIN_DEMAND = re.compile(
    r"\b(etsy|gumroad|fiverr|upwork|ebay|amazon|kdp|udemy|whop|lemonsqueezy|"
    r"creative market|facebook marketplace|tiktok shop|shopify app store)\b",
    re.IGNORECASE,
)

# Money changes hands directly and immediately — no funnel-building year.
_DIRECT_PAYMENT = re.compile(
    r"\b(invoice\w*|deposit\w*|retainer|day rate|flat fee|fixed[- ]price|"
    r"per[- ](?:gig|lead|report|audit|listing|render)|payment link|"
    r"charge\w* upfront|50% upfront)\b",
    re.IGNORECASE,
)

# Venture-shaped signals — the opposite of folding cash.
_SLOW_MONEY = re.compile(
    r"\b(raise (?:a|capital|funding|money)|seed round|series [ab]\b|venture|"
    r"runway|burn rate|network effects?|platform play|1[28]\+? months|"
    r"scale first|enterprise sales cycle|land[- ]and[- ]expand)\b",
    re.IGNORECASE,
)


_CATEGORY_BONUS: dict[IdeaCategory, float] = {
    # Fastest legal path from skill to invoice.
    IdeaCategory.PRODUCTIZED_SERVICES: 0.20,
    # Cash cycle measured in days once the data edge finds a mispricing.
    IdeaCategory.FLIPPING_ARBITRAGE: 0.18,
    # Build once, first sale within days on built-in-demand marketplaces.
    IdeaCategory.DIGITAL_PRODUCTS: 0.16,
    # Real cash but needs some working capital / ad testing.
    IdeaCategory.COMMERCE_OPS: 0.14,
    # Real cash but SEO/traffic lag pushes the first check out.
    IdeaCategory.LEAD_GENERATION: 0.12,
    # Everything else: 0 (cashflow_score is the board's axis, not universal).
}


# Score band that triggers the LLM second opinion.
LLM_VERIFY_LOWER = 0.35
LLM_VERIFY_UPPER = 0.70


def score_cashflow_heuristic(idea: Idea) -> float:
    """Cheap, deterministic time-to-first-dollar score in [0.0, 1.0]."""
    score = 0.15  # baseline — everything takes at least some hustle

    text_blob = " ".join(
        [
            idea.description or "",
            idea.mvp_scope or "",
            idea.tagline or "",
        ]
    )
    demand_blob = " ".join([text_blob, idea.market_analysis or "", " ".join(idea.tech_stack)])

    # First dollar lands in days, not quarters.
    if _FAST_MONEY.search(text_blob):
        score += 0.15

    # Near-zero starting capital.
    if _LOW_CAPITAL.search(text_blob):
        score += 0.15

    # Distribution with built-in buyers (marketplaces), text or stack.
    if _BUILTIN_DEMAND.search(demand_blob):
        score += 0.15

    # Direct, immediate payment mechanics.
    if _DIRECT_PAYMENT.search(text_blob):
        score += 0.10

    # Category bonus + any learned nudge (Scoreboard auto-tune; 0.0 unless opted in).
    score += _CATEGORY_BONUS.get(idea.category, 0.0)
    from project_forge.engine.scoreboard import learned_nudge

    score += learned_nudge("cashflow", idea.category)

    # Venture-shaped ideas are the anti-pattern for this axis.
    if _SLOW_MONEY.search(" ".join([text_blob, idea.market_analysis or ""])):
        score -= 0.15

    return max(0.0, min(1.0, score))


async def _llm_refine(idea: Idea, heuristic: float) -> float:
    """Ask the cheap LLM for a finer score when the heuristic is borderline.
    Falls back to the heuristic on any backend / parse failure."""
    backend = resolve_cheap_backend()
    if backend is None:
        return heuristic
    prompt = (
        "Rate this project idea's TIME-TO-FIRST-DOLLAR on a 0.0-1.0 scale: "
        "1.0 = someone could realistically invoice or make a first sale "
        "within days with near-zero capital; 0.0 = months of building and "
        "real capital before any money moves. Ignore long-term upside — "
        "score only speed and capital-lightness of the FIRST dollar. "
        "Respond with JSON only, single key 'score'.\n\n"
        f"## Idea: {idea.name}\n"
        f"**Tagline:** {idea.tagline}\n"
        f"**Description:** {idea.description}\n"
        f"**Market:** {idea.market_analysis}\n"
        f"**MVP:** {idea.mvp_scope}\n"
        f"**Tech:** {', '.join(idea.tech_stack)}\n\n"
        'Reply: {"score": 0.0-1.0}'
    )
    raw = (await asyncio.to_thread(backend.call, prompt) or "").strip()
    if "```json" in raw:
        raw = raw.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in raw:
        raw = raw.split("```", 1)[1].split("```", 1)[0].strip()
    try:
        data: dict[str, Any] = json.loads(raw)
        s = float(data["score"])
    except Exception:
        logger.info("cashflow LLM parse failed; sticking with heuristic")
        return heuristic
    return max(0.0, min(1.0, s))


async def score_cashflow(idea: Idea) -> float:
    """Heuristic-first, LLM tie-break in the borderline band."""
    heuristic = score_cashflow_heuristic(idea)
    if LLM_VERIFY_LOWER <= heuristic <= LLM_VERIFY_UPPER:
        return await _llm_refine(idea, heuristic)
    return heuristic


# --------------------------------------------------------------------------- #
# Bulk back-fill                                                              #
# --------------------------------------------------------------------------- #


async def score_pending_cashflow(db: Database, limit: int = 50) -> dict[str, Any]:
    """Score active cashflow-board ideas that don't yet have a
    cashflow_score. Scoped to CASHFLOW_CATEGORIES — the axis is the board's
    ranking, not a universal property. Idempotent; returns a summary."""
    placeholders = ",".join("?" * len(CASHFLOW_CATEGORIES))
    cur = await db.db.execute(
        f"SELECT id FROM ideas "  # noqa: S608
        f"WHERE cashflow_score IS NULL "
        f"AND category IN ({placeholders}) "
        f"AND status NOT IN ('archived', 'rejected') "
        f"ORDER BY generated_at DESC LIMIT ?",
        (*[c.value for c in CASHFLOW_CATEGORIES], limit),
    )
    rows = await cur.fetchall()
    scored = 0
    for r in rows:
        idea = await db.get_idea(r["id"])
        if idea is None:
            continue
        idea.cashflow_score = await score_cashflow(idea)
        await db.save_idea(idea)
        scored += 1
    return {"scored": scored, "limit": limit}
