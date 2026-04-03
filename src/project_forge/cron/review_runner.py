"""Automated idea review cycle for Project Forge.

Picks the oldest-unreviewed ideas in round-robin batches, reviews each,
records the verdict, and auto-archives ideas that score "kill" with high
confidence.

Works without an API key using heuristic analysis. Claude API enhances
with deeper reasoning when available, but is optional.
"""

import json
import logging
import os
from datetime import UTC, datetime

from project_forge.config import settings
from project_forge.models import Idea

logger = logging.getLogger(__name__)

_KILL_AUTO_ARCHIVE_THRESHOLD = 0.75


# ---------------------------------------------------------------------------
# Heuristic review — works without an API key
# ---------------------------------------------------------------------------


def heuristic_review(idea: Idea, category_counts: dict, total_ideas: int) -> dict:
    """Review an idea using local heuristic signals. No API required.

    Signals used: feasibility score, age, description quality, category saturation.
    """
    age_days = (datetime.now(UTC) - idea.generated_at).days
    desc_len = len(idea.description)
    cat_count = category_counts.get(idea.category.value, 0)
    avg_cat_count = (total_ideas / max(len(category_counts), 1)) if category_counts else 0

    # Score components (0.0 = bad, 1.0 = good)
    score_signal = idea.feasibility_score  # direct use
    age_signal = max(0.0, 1.0 - (age_days / 365))  # decays over a year
    quality_signal = min(1.0, desc_len / 500)  # longer desc = better quality signal
    saturation_signal = 1.0 - min(1.0, (cat_count / max(avg_cat_count * 2, 1)))

    composite = (
        score_signal * 0.4
        + age_signal * 0.25
        + quality_signal * 0.15
        + saturation_signal * 0.2
    )

    # Map composite to verdict
    if composite >= 0.75:
        verdict = "keep"
    elif composite >= 0.6:
        verdict = "strengthen"
    elif composite >= 0.45:
        verdict = "narrow"
    elif composite >= 0.3:
        verdict = "archive"
    else:
        verdict = "kill"

    # Confidence is lower for heuristic reviews
    confidence = min(0.85, composite * 0.9 + 0.1)
    if desc_len < 100:
        confidence = min(confidence, 0.5)

    # Override: very old + low score = archive with high confidence
    if age_days > 180 and idea.feasibility_score < 0.5:
        verdict = "archive"
        confidence = max(confidence, 0.8)
    if age_days > 365 and idea.feasibility_score < 0.6:
        verdict = "archive"
        confidence = max(confidence, 0.8)

    reasons = []
    if score_signal < 0.5:
        reasons.append(f"Low feasibility score ({idea.feasibility_score:.2f}).")
    if age_days > 90:
        reasons.append(f"Idea is {age_days} days old.")
    if cat_count > avg_cat_count * 1.5 and avg_cat_count > 0:
        reasons.append(f"Category '{idea.category.value}' is saturated ({cat_count} ideas).")
    if desc_len < 200:
        reasons.append("Description is thin — limited detail to assess.")
    if not reasons:
        reasons.append(f"Score {idea.feasibility_score:.2f}, {age_days} days old.")

    suggestions = []
    if verdict in ("narrow", "archive"):
        suggestions.append("Consider merging with similar ideas in this category.")
    if quality_signal < 0.5:
        suggestions.append("Expand the description and MVP scope for better evaluation.")
    if score_signal >= 0.7 and age_days > 60:
        suggestions.append("High potential but aging — prioritize or archive.")

    return {
        "verdict": verdict,
        "confidence": round(confidence, 2),
        "reasoning": " ".join(reasons) + " (heuristic review)",
        "suggestions": suggestions,
    }


# ---------------------------------------------------------------------------
# Claude-enhanced review — used when API key is available
# ---------------------------------------------------------------------------


def build_review_prompt(idea: Idea) -> str:
    """Build a prompt for Claude to review an existing idea."""
    age_days = (datetime.now(UTC) - idea.generated_at).days

    return (
        f"You are reviewing an existing project idea that was generated {age_days} days ago.\n"
        f"Determine if this idea is still viable or should be archived.\n\n"
        f"## Idea: {idea.name}\n"
        f"**Category:** {idea.category.value}\n"
        f"**Tagline:** {idea.tagline}\n"
        f"**Description:** {idea.description}\n"
        f"**Market Analysis:** {idea.market_analysis}\n"
        f"**MVP Scope:** {idea.mvp_scope}\n"
        f"**Tech Stack:** {', '.join(idea.tech_stack)}\n"
        f"**Original Feasibility Score:** {idea.feasibility_score}\n"
        f"**Generated:** {age_days} days ago ({idea.generated_at.strftime('%Y-%m-%d')})\n"
        f"**Status:** {idea.status}\n\n"
        f"## Instructions\n"
        f"Evaluate whether this idea is still worth pursuing. Consider:\n"
        f"- Has the market moved? Are there now competitors that didn't exist?\n"
        f"- Is the tech stack still relevant?\n"
        f"- Is the scope realistic for an MVP?\n"
        f"- Does this overlap with other common ideas that have been done?\n\n"
        f"Respond with JSON only (no markdown wrapping):\n"
        f'{{\n'
        f'  "verdict": "keep|strengthen|pivot|narrow|expand|archive|kill",\n'
        f'  "confidence": 0.0 to 1.0,\n'
        f'  "reasoning": "2-3 sentence explanation",\n'
        f'  "suggestions": ["actionable suggestion 1", "suggestion 2"]\n'
        f'}}\n\n'
        f'Verdict meanings:\n'
        f'- keep: idea is fine as-is\n'
        f'- strengthen: good idea, needs more detail or better framing\n'
        f'- pivot: core insight is good but direction should change\n'
        f'- narrow: scope too broad, needs focus\n'
        f'- expand: idea is too small, could be bigger\n'
        f'- archive: no longer relevant, overtaken by events\n'
        f'- kill: fundamentally flawed or completely superseded\n'
    )


async def _review_idea_with_api(idea: Idea, api_key: str, model: str) -> dict:
    """Send an idea to Claude for review. Returns verdict dict."""
    import anthropic

    prompt = build_review_prompt(idea)
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=model,
        max_tokens=2048,
        system="You are a senior technical reviewer. Respond ONLY with valid JSON.",
        messages=[{"role": "user", "content": prompt}],
    )

    raw = resp.content[0].text.strip()
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()

    data = json.loads(raw)
    return {
        "verdict": data.get("verdict", "keep"),
        "confidence": float(data.get("confidence", 0.5)),
        "reasoning": data.get("reasoning", ""),
        "suggestions": data.get("suggestions", []),
    }


# Keep old name as alias for tests that mock it
_review_idea = _review_idea_with_api


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _get_api_key() -> str:
    """Resolve API key from settings or environment. Returns empty string if none."""
    key = settings.anthropic_api_key
    if not key:
        key = os.environ.get("ANTHROPIC_API_KEY", "")
    return key


async def run_review_cycle(db, batch_size: int = 10, min_age_days: int = 7) -> dict:
    """Run one review cycle: fetch batch, review each, record results.

    Uses Claude API when available, falls back to heuristic review without it.
    Returns dict with 'reviewed' count and 'results' list.
    """
    ideas = await db.fetch_ideas_for_review(limit=batch_size, min_age_days=min_age_days)
    if not ideas:
        logger.info("No ideas due for review.")
        return {"reviewed": 0, "results": []}

    key = _get_api_key()
    use_api = bool(key)

    if not use_api:
        logger.info("No API key — using heuristic review for %d ideas.", len(ideas))
        cat_counts = await db.count_ideas_by_category()
        total_ideas = sum(cat_counts.values())

    results = []
    for idea in ideas:
        logger.info("Reviewing idea %s: %s", idea.id, idea.name)
        try:
            if use_api:
                review = await _review_idea_with_api(
                    idea, api_key=key, model=settings.anthropic_model,
                )
            else:
                review = heuristic_review(idea, cat_counts, total_ideas)

            await db.record_review(
                idea_id=idea.id,
                verdict=review["verdict"],
                confidence=review["confidence"],
                reasoning=review["reasoning"],
                suggestions=review["suggestions"],
            )

            # Auto-archive high-confidence kills/archives
            if review["verdict"] in ("kill", "archive") and review["confidence"] >= _KILL_AUTO_ARCHIVE_THRESHOLD:
                await db.update_idea_status(idea.id, "archived")
                logger.info("Auto-archived idea %s (kill @ %.0f%% confidence)",
                            idea.id, review["confidence"] * 100)

            results.append({
                "idea_id": idea.id,
                "name": idea.name,
                "status": "reviewed",
                "verdict": review["verdict"],
                "confidence": review["confidence"],
            })

        except Exception as exc:
            logger.error("Failed to review %s: %s", idea.id, exc)
            results.append({
                "idea_id": idea.id,
                "name": idea.name,
                "status": "error",
                "detail": str(exc),
            })

    logger.info("Review cycle complete: %d reviewed, %d errors",
                len(ideas),
                sum(1 for r in results if r["status"] == "error"))

    return {"reviewed": len(ideas), "results": results}
