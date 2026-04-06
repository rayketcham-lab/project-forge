"""Feasibility scoring for generated ideas.

Provides both legacy scoring helpers and an independent multi-signal scorer
that evaluates ideas without relying on LLM self-assessment.
"""

import re

from project_forge.engine.compare import _extract_keywords
from project_forge.models import Idea


def validate_score(idea: Idea) -> Idea:
    """Validate and clamp the feasibility score."""
    idea.feasibility_score = max(0.0, min(1.0, idea.feasibility_score))
    return idea


def is_high_value(idea: Idea, threshold: float = 0.7) -> bool:
    """Check if an idea meets the auto-scaffold threshold."""
    return idea.feasibility_score >= threshold


def score_summary(idea: Idea) -> str:
    """Return a human-readable score summary."""
    score = idea.feasibility_score
    if score >= 0.9:
        tier = "Excellent"
    elif score >= 0.7:
        tier = "Strong"
    elif score >= 0.5:
        tier = "Solid"
    elif score >= 0.3:
        tier = "Speculative"
    else:
        tier = "Moonshot"
    return f"{tier} ({score:.2f})"


# --- Independent Multi-Signal Scorer ---

# Technical terms that indicate concrete, specific ideas
_TECH_PATTERNS = [
    r"\b\w+\.py\b",
    r"\bsrc/",
    r"\btests?/",
    r"\b(API|CLI|JSON|YAML|SQL|HTTP|TLS|X\.509|ACME|OCSP|CRL|HSM|PQC)\b",
    r"\b(RFC\s?\d+)\b",
    r"\b(parse|validate|scan|check|verify|audit|rotate|renew|deploy|monitor)\b",
    r"\b(OpenSSL|Vault|Kubernetes|Docker|Prometheus|Grafana)\b",
]

# Overambition signals
_OVERAMBITION_SIGNALS = [
    "phase 1",
    "phase 2",
    "phase 3",
    "phase 4",
    "multi-tenant",
    "enterprise sso",
    "saas platform",
    "machine learning",
    "blockchain",
    "mobile app",
    "browser extension",
    "desktop client",
    "graphql and rest",
    "microservices architecture",
]

# Buzzwords that deflate specificity
_BUZZWORDS = [
    "synerg",
    "paradigm shift",
    "disrupt",
    "leverage",
    "next-generation",
    "ai-driven",
    "cutting-edge",
    "web3",
    "metaverse",
]


def _score_specificity(idea: Idea) -> float:
    """Score how specific and concrete an idea is (0.0-1.0).

    Rewards references to concrete technologies, protocols, file paths.
    Penalizes buzzwords and vague language.
    """
    full_text = f"{idea.description} {idea.mvp_scope}"
    if not full_text.strip():
        return 0.0

    score = 0.0
    words = full_text.split()
    word_count = len(words)

    # Length bonus
    if word_count >= 40:
        score += 0.25
    elif word_count >= 20:
        score += 0.15
    elif word_count >= 10:
        score += 0.05

    # Technical term bonus
    tech_hits = 0
    for pattern in _TECH_PATTERNS:
        if re.search(pattern, full_text, re.IGNORECASE):
            tech_hits += 1
    score += min(0.5, tech_hits * 0.1)

    # Tech stack diversity bonus
    stack_len = len(idea.tech_stack)
    if 2 <= stack_len <= 5:
        score += 0.15
    elif stack_len == 1:
        score += 0.05

    # Buzzword penalty
    text_lower = full_text.lower()
    bw_count = sum(1 for bw in _BUZZWORDS if bw in text_lower)
    score -= bw_count * 0.15

    return max(0.0, min(1.0, score))


def _score_novelty(idea: Idea, corpus: list[Idea] | None = None) -> float:
    """Score novelty as inverse of max similarity to any idea in the corpus.

    Returns 1.0 when corpus is empty (no overlap possible), drops as
    overlap with existing ideas increases.
    """
    if not corpus:
        return 1.0

    idea_text = f"{idea.name} {idea.tagline} {idea.description} {' '.join(idea.tech_stack)}"
    idea_keywords = _extract_keywords(idea_text)
    if not idea_keywords:
        return 0.5

    max_overlap = 0.0
    for existing in corpus:
        existing_text = f"{existing.name} {existing.tagline} {existing.description} {' '.join(existing.tech_stack)}"
        existing_keywords = _extract_keywords(existing_text)
        if not existing_keywords:
            continue
        matching = idea_keywords & existing_keywords
        union = idea_keywords | existing_keywords
        jaccard = len(matching) / len(union) if union else 0.0
        max_overlap = max(max_overlap, jaccard)

    return max(0.0, min(1.0, 1.0 - max_overlap))


def _score_scope_realism(idea: Idea) -> float:
    """Score whether the MVP scope is realistic for a 2-4 week build.

    Penalizes overambitious scope (many phases, too many technologies,
    enterprise-scale language in an MVP).
    """
    full_text = f"{idea.description} {idea.mvp_scope}".lower()
    score = 0.8  # Start optimistic

    # Overambition signal penalty
    overambition_hits = sum(1 for sig in _OVERAMBITION_SIGNALS if sig in full_text)
    score -= overambition_hits * 0.12

    # Too many tech stack items penalty
    if len(idea.tech_stack) > 5:
        score -= 0.15
    if len(idea.tech_stack) > 8:
        score -= 0.15

    # MVP scope that's too short = vague
    if len(idea.mvp_scope) < 30:
        score -= 0.1

    return max(0.0, min(1.0, score))


def score_idea(idea: Idea, corpus: list[Idea] | None = None) -> dict:
    """Compute independent multi-signal scores for an idea.

    Returns dict with specificity, novelty, scope_realism, and composite scores,
    all in the 0.0-1.0 range.
    """
    specificity = _score_specificity(idea)
    novelty = _score_novelty(idea, corpus)
    scope_realism = _score_scope_realism(idea)

    # Weighted composite: novelty matters most, then specificity, then scope
    composite = (novelty * 0.4) + (specificity * 0.35) + (scope_realism * 0.25)
    composite = max(0.0, min(1.0, round(composite, 3)))

    return {
        "specificity": round(specificity, 3),
        "novelty": round(novelty, 3),
        "scope_realism": round(scope_realism, 3),
        "composite": composite,
    }
