"""Compare a forge idea against an existing GitHub repository."""

import re

from project_forge.models import Idea

# Common stop words to exclude from keyword matching
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "is",
        "it",
        "as",
        "be",
        "was",
        "are",
        "that",
        "this",
        "has",
        "had",
        "not",
        "no",
        "all",
        "can",
        "will",
        "do",
        "if",
        "so",
        "up",
        "out",
        "about",
        "into",
        "over",
        "after",
        "build",
        "tool",
        "based",
        "using",
        "support",
        "new",
        "use",
    }
)


def _extract_keywords(text: str) -> set[str]:
    """Extract meaningful keywords from text, lowercased."""
    words = re.findall(r"[a-zA-Z0-9][\w\-\.]*[a-zA-Z0-9]|[a-zA-Z0-9]", text.lower())
    return {w for w in words if w not in _STOP_WORDS and len(w) > 1}


def compare_idea_to_repo(idea: Idea, repo_details: dict) -> dict:
    """Compare an idea against a GitHub repo's metadata.

    Returns dict with overlap_score (0-1), verdict, reason, matching_keywords.
    """
    # Build keyword sets from idea
    idea_text = " ".join(
        [
            idea.name,
            idea.tagline,
            idea.description,
            " ".join(idea.tech_stack),
        ]
    )
    idea_keywords = _extract_keywords(idea_text)

    # Build keyword sets from repo
    repo_text = " ".join(
        filter(
            None,
            [
                repo_details.get("name", ""),
                repo_details.get("description", ""),
                " ".join(repo_details.get("topics", [])),
                repo_details.get("language", "") or "",
                repo_details.get("readme", ""),
            ],
        )
    )
    repo_keywords = _extract_keywords(repo_text)

    # Calculate overlap
    if not idea_keywords or not repo_keywords:
        return {
            "overlap_score": 0.0,
            "verdict": "new",
            "reason": "Insufficient data for comparison.",
            "matching_keywords": [],
        }

    matching = idea_keywords & repo_keywords
    # Weighted toward idea coverage (idea_keywords/repo_keywords guaranteed non-empty)
    idea_coverage = len(matching) / len(idea_keywords)
    repo_coverage = len(matching) / len(repo_keywords)
    overlap_score = round((idea_coverage * 0.7 + repo_coverage * 0.3), 2)

    # Determine verdict
    if overlap_score >= 0.5:
        verdict = "duplicate"
    elif overlap_score >= 0.25:
        verdict = "enhance"
    else:
        verdict = "new"

    # Build reason
    matching_list = sorted(matching)
    if verdict == "duplicate":
        reason = (
            f"High overlap ({overlap_score:.0%}) with {repo_details.get('name', 'repo')}. "
            f"Shared concepts: {', '.join(matching_list[:10])}. "
            "This idea likely duplicates existing functionality."
        )
    elif verdict == "enhance":
        reason = (
            f"Partial overlap ({overlap_score:.0%}) with {repo_details.get('name', 'repo')}. "
            f"Shared concepts: {', '.join(matching_list[:10])}. "
            "This idea could enhance the existing project with new capabilities."
        )
    else:
        reason = (
            f"Low overlap ({overlap_score:.0%}) with {repo_details.get('name', 'repo')}. "
            "This appears to be a distinct, new project idea."
        )

    return {
        "overlap_score": overlap_score,
        "verdict": verdict,
        "reason": reason,
        "matching_keywords": matching_list,
    }
