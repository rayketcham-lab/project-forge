"""Vertical (industry) inference for cross-cutting drill-down.

The existing IdeaCategory axis is technical (security-tool, observability,
pqc-cryptography, ...). Verticals add a parallel INDUSTRY axis so the
explore UI can slice "PKI ideas in healthcare" or "automation ideas for
education" without changing the data model.

Inference is keyword-based and runs at query time over name + tagline +
description. Multiple verticals per idea are valid; zero verticals is
also valid (idea is "general / horizontal" — shown alongside any
vertical filter).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from project_forge.models import Idea


# Map vertical slug → set of word-boundary keyword patterns. Keep slugs
# stable; the dashboard chip row references them by literal value.
VERTICAL_KEYWORDS: dict[str, list[str]] = {
    "government": [
        "government",
        "federal",
        "agency",
        "fedramp",
        "fisma",
        "dod",
        "cisa",
        "ato",
        "fips 140",
        "stig",
        "section 508",
        "civic",
        "municipality",
        "federal agency",
    ],
    "healthcare": [
        "healthcare",
        "hospital",
        "patient",
        "hipaa",
        "clinical",
        "ehr",
        "phi",
        "medical",
        "physician",
        "pharma",
        "fda",
    ],
    "education": [
        "education",
        "school",
        "university",
        "campus",
        "ferpa",
        "k-12",
        "k12",
        "student",
        "teacher",
        "lms",
        "research lab",
        "edtech",
    ],
    "finance": [
        "bank",
        "banking",
        "fintech",
        "payment",
        "trading",
        "pci-dss",
        "pci dss",
        "treasury",
        "ledger",
        "broker",
        "fdic",
        "swift",
        "ofac",
        "aml",
        "kyc",
    ],
    "retail": [
        "retail",
        "ecommerce",
        "e-commerce",
        "shopping cart",
        "checkout",
        "merchant",
        "point of sale",
        "pos system",
        "loyalty program",
        "inventory",
    ],
    "hospitality": [
        "hotel",
        "restaurant",
        "guest",
        "booking",
        "reservation",
        "hospitality",
        "travel",
        "loyalty",
        "concierge",
    ],
    "manufacturing": [
        "manufacturing",
        "factory floor",
        "ot/ics",
        "industrial control",
        "scada",
        "iiot",
        "assembly line",
        "industrial iot",
    ],
    "energy": [
        "utility",
        "smart grid",
        "smart meter",
        "energy",
        "power grid",
        "substation",
        "ferc",
    ],
    "telco": [
        "telecom",
        " 5g ",
        "5g core",
        "carrier",
        "isp",
        "mobile network",
        "mvno",
        "telco",
    ],
}

KNOWN_VERTICALS: frozenset[str] = frozenset(VERTICAL_KEYWORDS.keys())

# Pre-compile word-boundary patterns. We use \b so "rambank" doesn't match
# "bank". Multi-word keywords get an exact substring match (still
# case-insensitive) since \b doesn't work cleanly across spaces.
_VERTICAL_PATTERNS: dict[str, list[re.Pattern]] = {}
for _slug, _keywords in VERTICAL_KEYWORDS.items():
    _patterns: list[re.Pattern] = []
    for kw in _keywords:
        if " " in kw or "/" in kw or "-" in kw:
            _patterns.append(re.compile(re.escape(kw), re.IGNORECASE))
        else:
            # Allow natural pluralization: "hotel" matches "hotels".
            _patterns.append(re.compile(rf"\b{re.escape(kw)}s?\b", re.IGNORECASE))
    _VERTICAL_PATTERNS[_slug] = _patterns


def _haystack(idea: Idea) -> str:
    return f"{idea.name}\n{idea.tagline}\n{idea.description}"


# Process-level cache: ideas are effectively immutable for the inference
# inputs (name + tagline + description). Cache key = idea.id; eviction is
# size-bounded so a runaway DB doesn't bloat memory. Without this, the
# dashboard's "Browse by Industry" panel takes ~600ms per request to run
# 500 ideas × 9 verticals × ~10 patterns each through regex.
_INFER_CACHE: dict[str, list[str]] = {}
_INFER_CACHE_MAX = 8192


def _infer_uncached(idea: Idea) -> list[str]:
    text = _haystack(idea)
    hits: list[str] = []
    for slug, patterns in _VERTICAL_PATTERNS.items():
        for p in patterns:
            if p.search(text):
                hits.append(slug)
                break
    return sorted(hits)


def infer_verticals(idea: Idea) -> list[str]:
    """Return the list of vertical slugs an idea matches, sorted alphabetically.

    Cached on idea.id. The cache is fine for normal browsing traffic; if
    an idea's name/tagline/description is mutated, call invalidate_vertical_cache.
    """
    cache_key = idea.id
    cached = _INFER_CACHE.get(cache_key)
    if cached is not None:
        return cached

    result = _infer_uncached(idea)

    if len(_INFER_CACHE) >= _INFER_CACHE_MAX:
        # Simple FIFO eviction — drop the oldest 1/8 of entries.
        for k in list(_INFER_CACHE.keys())[: _INFER_CACHE_MAX // 8]:
            _INFER_CACHE.pop(k, None)
    _INFER_CACHE[cache_key] = result
    return result


def invalidate_vertical_cache(idea_id: str | None = None) -> None:
    """Drop a cached inference result. Pass None to clear the whole cache."""
    if idea_id is None:
        _INFER_CACHE.clear()
    else:
        _INFER_CACHE.pop(idea_id, None)


def matches_vertical(idea: Idea, vertical: str) -> bool:
    """True iff the idea matches the given vertical slug.

    Short-circuits on first matching pattern — does NOT compute the full
    vertical set for this idea. Use this when filtering by a single
    vertical; use infer_verticals when you need all matches.
    """
    patterns = _VERTICAL_PATTERNS.get(vertical)
    if not patterns:
        return False
    text = _haystack(idea)
    return any(p.search(text) for p in patterns)
