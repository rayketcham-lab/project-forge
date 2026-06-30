"""Consolidate the self-improvement (Think Tank) idea list.

The super-idea synthesis used to bundle concrete code tasks into floaty
"[SUPER] X + Y synthesized into one platform" ideas, flooding the Think Tank.
That source is now fixed (super_ideas excludes SELF_IMPROVEMENT), but the
existing junk needs cleaning up. This archives the synthesis noise and
deduplicates near-identical base proposals so the Think Tank shows a clean,
reasonable list. Idempotent — safe to re-run.
"""

from __future__ import annotations

import re

from project_forge.models import IdeaCategory
from project_forge.storage.db import Database

_INACTIVE = ("archived", "rejected", "implemented")

# Garbled names left by the generic combinatoric/crossover generator being
# (mis)applied to self-improvement — e.g. "Dashboard UX Improvements And for
# Performance", "CI Pipeline Gap Detection for Reliability". That leak is fixed
# at the source; this archives the existing artifacts. Matches "<concept> [and/
# in/of X] for <SI-domain>".
_CROSSOVER_ARTIFACT = re.compile(
    r"\b(?:and|in|of\s+\w+)?\s*for\s+(performance|reliability|developer|devsecops|test|engineering)\b",
    re.IGNORECASE,
)


def _normalize(name: str) -> str:
    """Normalize a proposal name for dedup (lowercase, collapse whitespace)."""
    return re.sub(r"\s+", " ", name.strip().lower())


async def consolidate_self_improvement(db: Database) -> dict:
    """Archive floaty [SUPER] self-improvement ideas and dedupe base ones.

    Returns a report: {archived_super, archived_dupes, kept}. Idempotent.
    """
    si = await db.list_ideas(category=IdeaCategory.SELF_IMPROVEMENT, limit=1000)
    # Newest first (list_ideas orders by generated_at DESC), so the first time
    # we see a normalized name is the freshest copy — keep it, archive older dupes.
    active = [i for i in si if i.status not in _INACTIVE]

    archived_super = 0
    archived_dupes = 0
    archived_garbled = 0
    seen: set[str] = set()

    for idea in active:
        if idea.name.startswith("[SUPER]"):
            # Synthesis noise — never useful for self-improvement.
            await db.update_idea_status(idea.id, "archived")
            archived_super += 1
            continue
        if _CROSSOVER_ARTIFACT.search(idea.name):
            await db.update_idea_status(idea.id, "archived")
            archived_garbled += 1
            continue
        key = _normalize(idea.name)
        if key in seen:
            await db.update_idea_status(idea.id, "archived")
            archived_dupes += 1
        else:
            seen.add(key)

    return {
        "archived_super": archived_super,
        "archived_garbled": archived_garbled,
        "archived_dupes": archived_dupes,
        "kept": len(seen),
    }
