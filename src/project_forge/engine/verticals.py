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
        "government", "federal", "agency", "fedramp", "fisma",
        "dod", "cisa", "ato", "fips 140", "stig", "section 508",
        "civic", "municipality", "federal agency",
    ],
    "healthcare": [
        "healthcare", "hospital", "patient", "hipaa", "clinical",
        "ehr", "phi", "medical", "physician", "pharma", "fda",
    ],
    "education": [
        "education", "school", "university", "campus", "ferpa",
        "k-12", "k12", "student", "teacher", "lms",
        "research lab", "edtech",
    ],
    "finance": [
        "bank", "banking", "fintech", "payment", "trading",
        "pci-dss", "pci dss", "treasury", "ledger", "broker",
        "fdic", "swift", "ofac", "aml", "kyc",
    ],
    "retail": [
        "retail", "ecommerce", "e-commerce", "shopping cart",
        "checkout", "merchant", "point of sale", "pos system",
        "loyalty program", "inventory",
    ],
    "hospitality": [
        "hotel", "restaurant", "guest", "booking", "reservation",
        "hospitality", "travel", "loyalty", "concierge",
    ],
    "manufacturing": [
        "manufacturing", "factory floor", "ot/ics", "industrial control",
        "scada", "iiot", "assembly line", "industrial iot",
    ],
    "energy": [
        "utility", "smart grid", "smart meter", "energy",
        "power grid", "substation", "ferc",
    ],
    "telco": [
        "telecom", " 5g ", "5g core", "carrier", "isp",
        "mobile network", "mvno", "telco",
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


def infer_verticals(idea: Idea) -> list[str]:
    """Return the list of vertical slugs an idea matches, sorted alphabetically."""
    text = _haystack(idea)
    hits: list[str] = []
    for slug, patterns in _VERTICAL_PATTERNS.items():
        for p in patterns:
            if p.search(text):
                hits.append(slug)
                break
    return sorted(hits)


def matches_vertical(idea: Idea, vertical: str) -> bool:
    """True iff the idea matches the given vertical slug."""
    if vertical not in KNOWN_VERTICALS:
        return False
    return vertical in infer_verticals(idea)
