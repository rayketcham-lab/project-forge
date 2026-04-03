"""Fuzzy deduplication for self-improvement ideas.

Uses token-set overlap on normalized taglines to detect near-duplicate
ideas like "dashboard UX improvements — tailored for developer experience"
vs "dashboard UX improvements — tailored for test engineering".
"""

# Similarity threshold: ideas above this score are considered duplicates
SIMILARITY_THRESHOLD = 0.7


def _normalize(text: str) -> str:
    """Strip Claude's 'tailored for X' suffix pattern and normalize."""
    # Remove everything after em dash, en dash, or double hyphen (Claude generation artifact)
    for sep in ("\u2014", "\u2013", "--"):
        if sep in text:
            text = text[: text.index(sep)]
    return text.strip().lower()


def _tokenize(text: str) -> set[str]:
    """Normalize and tokenize a tagline into a set of lowercase words."""
    return set(_normalize(text).split())


def tagline_similarity(a: str, b: str) -> float:
    """Return 0.0–1.0 similarity score between two taglines using token overlap.

    Uses Jaccard-like similarity: |intersection| / |union|.
    Returns 1.0 for identical (including both empty), 0.0 for no overlap.
    """
    tokens_a = _tokenize(a)
    tokens_b = _tokenize(b)

    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0

    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)
