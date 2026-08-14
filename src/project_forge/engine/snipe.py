"""Snipe scoring + angles — can we wedge into a market-PROVEN incumbent.

The Sniper board's axis. Distinct from feasibility (can we build it),
fundability (can we sell it), and ambition (does it push the ceiling).
``snipe_score`` rewards a sharp competitive-displacement play against an
incumbent whose demand is already de-risked by real money.

What a high snipe scores on:
  - **A named real incumbent** (target_incumbent set) — the non-negotiable
    gate. A snipe with no named comp is just a regular idea.
  - **Proven demand** — grounded traction signal in the text ($/ARR/funding,
    GitHub stars, HN points, "category leader").
  - **A structural wedge** — overpriced, bloated, enterprise-only, closed,
    legacy, unbundle-able, open-source-able.
  - **A why-now catalyst** — AI-native rebuild, a price hike, a PE buyout, a
    new regulation the incumbent is slow on.
  - **A focused beachhead** — narrow enough for a small team to ship.

Two-stage like the other scorers: a free deterministic heuristic, then a
cheap LLM tie-break only in the borderline band.

Angles are the variety engine — like Claude Lab rotates artifact shapes,
the Sniper board rotates the *kind* of wedge so it doesn't pitch 50
"cheaper clones". The chosen angle is persisted in the idea's
``artifact_type`` column (disjoint vocabulary from the Claude Lab shapes).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import TYPE_CHECKING, Any

from project_forge.engine.llm_backend import resolve_cheap_backend
from project_forge.models import Idea

if TYPE_CHECKING:
    from project_forge.storage.db import Database

logger = logging.getLogger(__name__)


# The wedge angles. Stored in idea.artifact_type for snipe-mode ideas.
SNIPE_ANGLES = [
    "price-snipe",  # incumbent got greedy / PE-owned / enshittified
    "unbundle",  # extract one overpriced feature of a bloated suite
    "down-market",  # enterprise-only pricing locks out the SMB long tail
    "vertical",  # horizontal tool blind to a specific trade's workflow
    "ai-native",  # the core workflow is now 10x cheaper with LLMs
    "open-source",  # closed / no-API incumbent ripe for an OSS challenger
    "compliance-shift",  # a new mandate the incumbent is slow to address
]

_ANGLE_PROMPTS: dict[str, str] = {
    "price-snipe": (
        "ANGLE — PRICE SNIPE: the incumbent has gotten greedy (price hikes, "
        "PE ownership, enshittification, surprise fees). Pitch the version "
        "that wins on honest, predictable, dramatically lower pricing. Name "
        "the specific pricing pain you're undercutting."
    ),
    "unbundle": (
        "ANGLE — UNBUNDLE: the incumbent is a bloated suite where customers "
        "pay for 20 features to use 2. Extract ONE overpriced feature and "
        "pitch it as a sharp standalone tool at a fraction of the suite price."
    ),
    "down-market": (
        "ANGLE — DOWN-MARKET: the incumbent's pricing + complexity is built "
        "for the enterprise and locks out the huge SMB / solo long tail. "
        "Pitch the stripped, self-serve, affordable version for the segment "
        "the incumbent ignores."
    ),
    "vertical": (
        "ANGLE — VERTICAL: the incumbent is a horizontal tool blind to a "
        "specific trade's real workflow. Pitch the deeply-vertical version "
        "for one named industry that fits like a glove where the generic "
        "tool fights the user."
    ),
    "ai-native": (
        "ANGLE — AI-NATIVE REBUILD: the incumbent's core workflow is manual "
        "or rules-based and is now 10x cheaper/faster with LLMs. Pitch the "
        "AI-native rebuild that makes the incumbent's hardest task trivial. "
        "State exactly which workflow collapses."
    ),
    "open-source": (
        "ANGLE — OPEN-SOURCE / DEV-FIRST: the incumbent is closed, has no API, "
        "or holds data hostage. Pitch the open-source / self-hostable / "
        "API-first challenger that wins on control, transparency, and "
        "extensibility. Cite the OSS challengers already gaining traction."
    ),
    "compliance-shift": (
        "ANGLE — COMPLIANCE SHIFT: a new regulation, standard, or mandate is "
        "landing (or a deadline looming) that the incumbent is slow to meet. "
        "Pitch the tool that nails the new requirement first. Name the "
        "specific mandate / standard and the deadline pressure."
    ),
}


# --- Heuristic signal patterns ------------------------------------------- #

# Proven-demand markers: dollars, traction nouns, grounded numbers.
_DEMAND_PROOF = re.compile(
    r"(\$\s?\d|\b\d[\d,.]*\s?(k|m|b|million|billion)\b|"
    r"\barr\b|\bmrr\b|\bfund(ed|ing)\b|\braised\b|\bvaluation\b|"
    r"\bacquir\w+|\bipo\b|\bunicorn\b|\bcategory leader\b|"
    r"\bmarket leader\b|\d[\d,]*\s*(stars?|★)|\bshow hn\b|"
    r"\d[\d,]*\s*(users|customers|downloads|installs|subscribers))",
    re.IGNORECASE,
)

# Structural-wedge markers: a real opening, not "it's bad".
_WEDGE = re.compile(
    r"\b(overpriced|too expensive|expensive|bloated|clunky|enshittif\w+|"
    r"enterprise[\s-]?only|no api|closed[\s-]?source|proprietary|legacy|"
    r"lock[\s-]?in|vendor lock|unbundl\w+|open[\s-]?source|self[\s-]?host\w*|"
    r"cheaper|undercut\w*|smb|long tail|down[\s-]?market|api[\s-]?first)\b",
    re.IGNORECASE,
)

# Why-now catalysts.
_WHY_NOW = re.compile(
    r"\b(ai[\s-]?native|ai[\s-]?powered|llm|gpt|generative|"
    r"new regulation|mandate|deadline|sunset\w*|deprecat\w+|"
    r"price hike|acquisition|private equity|\bpe[\s-]?owned\b|post[\s-]?quantum|pqc)\b",
    re.IGNORECASE,
)

# Beachhead / focus markers.
_BEACHHEAD = re.compile(
    r"\b(beachhead|wedge|niche|focused|narrow|one [a-z]+ first|single[\s-]?purpose)\b",
    re.IGNORECASE,
)

LLM_VERIFY_LOWER = 0.40
LLM_VERIFY_UPPER = 0.75


def score_snipe_heuristic(idea: Idea) -> float:
    """Cheap, deterministic snipe-ability score in [0.0, 1.0]."""
    score = 0.15

    # The gate: a named, real incumbent. Without it, this isn't a snipe.
    if (idea.target_incumbent or "").strip():
        score += 0.25
    else:
        score -= 0.10

    blob = " ".join(
        [
            idea.description or "",
            idea.market_analysis or "",
            idea.mvp_scope or "",
            idea.tagline or "",
        ]
    )

    # Proven demand — strongest signal, count distinct hits up to 4.
    demand_hits = len({m.group(0).lower() for m in _DEMAND_PROOF.finditer(blob)})
    score += min(demand_hits, 4) * 0.05

    # Structural wedge present.
    if _WEDGE.search(blob):
        score += 0.15

    # Why-now catalyst present.
    if _WHY_NOW.search(blob):
        score += 0.10

    # Focused beachhead.
    if _BEACHHEAD.search(blob):
        score += 0.05

    # Learned nudge (v0.17 Scoreboard auto-tune; 0.0 unless opted in).
    from project_forge.engine.scoreboard import learned_nudge

    score += learned_nudge("snipe", idea.category)

    return max(0.0, min(1.0, score))


async def _llm_refine(idea: Idea, heuristic: float) -> float:
    """Cheap LLM tie-break for borderline snipes."""
    backend = resolve_cheap_backend()
    if backend is None:
        return heuristic
    prompt = (
        "Rate this idea's *snipe-ability* on a 0.0-1.0 scale: how strong is "
        "it as a competitive-displacement play against a market-proven "
        "incumbent? Higher = the incumbent's demand is clearly real AND there "
        "is a sharp, credible, focused wedge to take a slice. Lower = no real "
        "incumbent, vague weakness, or a boil-the-ocean rebuild. Be "
        "conservative. Respond with JSON only, single key 'score'.\n\n"
        f"## Idea: {idea.name}\n"
        f"**Target incumbent:** {idea.target_incumbent or '(none named)'}\n"
        f"**Tagline:** {idea.tagline}\n"
        f"**Description:** {idea.description}\n"
        f"**Market:** {idea.market_analysis}\n\n"
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
        logger.info("snipe LLM parse failed; sticking with heuristic")
        return heuristic
    return max(0.0, min(1.0, s))


async def score_snipe(idea: Idea) -> float:
    """Heuristic-first, LLM tie-break in the borderline band."""
    heuristic = score_snipe_heuristic(idea)
    if LLM_VERIFY_LOWER <= heuristic <= LLM_VERIFY_UPPER:
        return await _llm_refine(idea, heuristic)
    return heuristic


async def pick_least_used_angle(db: Database, category) -> str:
    """Pick the SNIPE_ANGLES entry with the fewest active snipe ideas in
    this category — same rotation discipline as the artifact picker."""
    cur = await db.db.execute(
        "SELECT artifact_type, COUNT(*) c FROM ideas "
        "WHERE category = ? AND generation_mode = 'snipe' "
        "AND status NOT IN ('archived', 'rejected') "
        "AND artifact_type IS NOT NULL "
        "GROUP BY artifact_type",
        (category.value,),
    )
    rows = await cur.fetchall()
    counts = {r["artifact_type"]: int(r["c"]) for r in rows}
    return min(SNIPE_ANGLES, key=lambda a: (counts.get(a, 0), SNIPE_ANGLES.index(a)))


async def score_pending_snipe(db: Database, limit: int = 50) -> dict[str, Any]:
    """Bulk back-fill of snipe ideas missing a score. Idempotent."""
    cur = await db.db.execute(
        "SELECT id FROM ideas "
        "WHERE generation_mode = 'snipe' AND snipe_score IS NULL "
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
        idea.snipe_score = await score_snipe(idea)
        await db.save_idea(idea)
        scored += 1
    return {"scored": scored, "limit": limit}
