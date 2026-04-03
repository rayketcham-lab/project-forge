"""Universal quality review gate for all generated ideas.

Every idea — self-improvement, regular, cross-category, expanded — must pass
quality review before being saved. The review checks:
1. Logistics: Is the description specific enough? Does it have actionable scope?
2. Practicality: Does it make sense? Is the scope realistic?
3. Benefit: Is it a real concept or buzzword soup?

For self-improvement ideas: must reference project-forge internals, not new projects.
"""

import logging
import re
from dataclasses import dataclass, field

from project_forge.models import IdeaCategory

logger = logging.getLogger(__name__)

# Buzzword patterns that indicate low substance
_BUZZWORDS = [
    "synerg",
    "paradigm shift",
    "disrupt",
    "leverage",
    "next-generation",
    "cloud-native",
    "ai-driven",
    "blockchain",
    "web3",
    "metaverse",
    "cutting-edge",
]

# SI-specific: signals that it's a new project, not a code improvement
_NEW_PROJECT_SIGNALS = [
    "phase 1",
    "phase 2",
    "ship to",
    "early adopters",
    "multi-tenant",
    "enterprise sso",
    "saas",
    "willing to pay",
    "competitive landscape",
    "market demand",
    "go-to-market",
    "pricing model",
    "weeks 1-2",
    "weeks 3-4",
]

_MIN_DESCRIPTION_LEN = 50
_MIN_MVP_SCOPE_LEN = 20


@dataclass
class ReviewResult:
    """Result of a quality review."""

    passed: bool
    score: float  # 0.0–1.0
    reasons: list[str] = field(default_factory=list)


def _score_specificity(text: str) -> float:
    """Score how specific and concrete a text is (0.0–1.0)."""
    if not text:
        return 0.0
    score = 0.0
    words = text.split()
    word_count = len(words)

    # Length bonus (longer = more detail, up to a point)
    if word_count >= 30:
        score += 0.3
    elif word_count >= 15:
        score += 0.2
    elif word_count >= 8:
        score += 0.1

    # Technical terms bonus (specific nouns, not buzzwords)
    tech_patterns = [
        r"\b\w+\.py\b",  # file paths
        r"\bsrc/",  # source paths
        r"\btests?/",  # test paths
        r"\b(API|CLI|JSON|YAML|SQL|HTTP|TLS|X\.509)\b",
        r"\b(parse|validate|scan|check|verify|audit)\b",
    ]
    for pattern in tech_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            score += 0.1

    # Cap at 1.0
    return min(1.0, score)


def _count_buzzwords(text: str) -> int:
    """Count buzzword occurrences in text."""
    text_lower = text.lower()
    return sum(1 for bw in _BUZZWORDS if bw in text_lower)


def _has_new_project_signals(text: str) -> bool:
    """Check if text contains new-project proposal language."""
    text_lower = text.lower()
    return any(signal in text_lower for signal in _NEW_PROJECT_SIGNALS)


def review_idea(idea) -> ReviewResult:
    """Review an idea for quality, logistics, practicality, and benefit.

    Returns a ReviewResult with passed, score, and reasons.
    """
    reasons = []
    scores = []

    full_text = f"{idea.description} {idea.mvp_scope} {idea.market_analysis}"

    # --- Check 1: Description length ---
    if len(idea.description) < _MIN_DESCRIPTION_LEN:
        reasons.append(f"Description too short ({len(idea.description)} chars, need {_MIN_DESCRIPTION_LEN}+)")
        scores.append(0.0)
    else:
        desc_score = _score_specificity(idea.description)
        scores.append(desc_score)

    # --- Check 2: MVP scope specificity ---
    if len(idea.mvp_scope) < _MIN_MVP_SCOPE_LEN:
        reasons.append(f"MVP scope too vague ({len(idea.mvp_scope)} chars, need {_MIN_MVP_SCOPE_LEN}+)")
        scores.append(0.0)
    else:
        mvp_score = _score_specificity(idea.mvp_scope)
        scores.append(mvp_score)

    # --- Check 3: Buzzword density ---
    bw_count = _count_buzzwords(full_text)
    if bw_count >= 3:
        reasons.append(f"Too many buzzwords ({bw_count}): low substance")
        scores.append(0.0)
    else:
        scores.append(1.0 - (bw_count * 0.3))

    # --- Check 4: SI-specific: new-project signals ---
    if idea.category == IdeaCategory.SELF_IMPROVEMENT:
        if _has_new_project_signals(full_text):
            reasons.append("Self-improvement idea contains new-project language")
            scores.append(0.0)
        else:
            scores.append(0.8)

    # --- Compute final score ---
    final_score = sum(scores) / len(scores) if scores else 0.0
    passed = final_score >= 0.4 and len(reasons) == 0

    if not passed:
        logger.info("Idea '%s' failed review (score=%.2f): %s", idea.name, final_score, "; ".join(reasons))

    return ReviewResult(passed=passed, score=round(final_score, 2), reasons=reasons)
