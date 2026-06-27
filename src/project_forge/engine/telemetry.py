"""Generation telemetry — read-only analytics over filtered_ideas + ideas.

The eyes for self-improvement. Every function here is pure: takes a
Database, returns a structured value, no side effects, no DB writes.

Used by:
- engine/introspect.py (gather_generation_signals)
- engine/prompts.py (rejection-aware prompt summary, Phase 4)
- scripts/shadow_generate.py (metric deltas)

See issues #54, #55, #56, #57.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from project_forge.engine.super_ideas import _NAME_STOP_WORDS
from project_forge.models import IdeaCategory

if TYPE_CHECKING:
    from project_forge.storage.db import Database


_TAGLINE_SIM_RE = re.compile(r"tagline_similarity:(\d+\.\d+)")
_GENERIC_NOUNS = frozenset(
    {
        "tool",
        "tools",
        "platform",
        "system",
        "service",
        "engine",
        "manager",
        "framework",
        "suite",
        "module",
        "library",
        "agent",
        "app",
        "apps",
    }
)


async def filter_rate_by_category(
    db: Database,
    days: int = 7,
) -> dict[IdeaCategory, float]:
    """Return per-category filter rate over the last `days`.

    Rate = filtered / (filtered + accepted). Higher = more rejections,
    indicating saturation in that category.
    """
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()

    cursor = await db.db.execute(
        "SELECT idea_category, COUNT(*) FROM filtered_ideas WHERE filtered_at >= ? GROUP BY idea_category",
        (cutoff,),
    )
    filtered_counts = {row[0]: row[1] for row in await cursor.fetchall()}

    cursor = await db.db.execute(
        "SELECT category, COUNT(*) FROM ideas WHERE generated_at >= ? GROUP BY category",
        (cutoff,),
    )
    accepted_counts = {row[0]: row[1] for row in await cursor.fetchall()}

    rates: dict[IdeaCategory, float] = {}
    all_cats = set(filtered_counts) | set(accepted_counts)
    for cat_str in all_cats:
        try:
            cat = IdeaCategory(cat_str)
        except ValueError:
            continue
        f = filtered_counts.get(cat_str, 0)
        a = accepted_counts.get(cat_str, 0)
        total = f + a
        if total == 0:
            continue
        rates[cat] = f / total
    return rates


async def saturation_per_concept(
    db: Database,
    days: int = 30,
    top_n: int = 10,
) -> list[tuple[str, int]]:
    """Return ranked list of saturated keywords from rejected idea names.

    Mines `filtered_ideas.idea_name` for tokens (5+ chars, not stop-words,
    not generic nouns). Returns descending by frequency.
    """
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()

    cursor = await db.db.execute(
        "SELECT idea_name FROM filtered_ideas WHERE filtered_at >= ?",
        (cutoff,),
    )
    rows = await cursor.fetchall()

    counter: Counter[str] = Counter()
    for row in rows:
        name = row[0] or ""
        for word in re.findall(r"[A-Za-z]{5,}", name):
            w = word.lower()
            if w in _NAME_STOP_WORDS or w in _GENERIC_NOUNS:
                continue
            counter[w] += 1

    return counter.most_common(top_n)


async def novelty_trend(
    db: Database,
    days: int = 30,
) -> list[tuple[str, float]]:
    """Return per-day average tagline-similarity score from rejections.

    Rising avg = engine producing more near-duplicates = novelty falling.
    """
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()

    cursor = await db.db.execute(
        "SELECT filtered_at, filter_reason FROM filtered_ideas "
        "WHERE filtered_at >= ? AND filter_reason LIKE 'duplicate:tagline_similarity%'",
        (cutoff,),
    )
    rows = await cursor.fetchall()

    by_day: dict[str, list[float]] = {}
    for filtered_at, reason in rows:
        m = _TAGLINE_SIM_RE.search(reason or "")
        if not m:
            continue
        try:
            score = float(m.group(1))
        except ValueError:
            continue
        day = filtered_at[:10]
        by_day.setdefault(day, []).append(score)

    return sorted(
        ((day, sum(scores) / len(scores)) for day, scores in by_day.items()),
        key=lambda x: x[0],
    )


async def diversity_lever_usage(
    db: Database,
    days: int = 7,  # noqa: ARG001 — schema-stable interface
) -> dict[str, float]:
    """Return diversity lever usage percentages.

    Phase 1 returns a default-zero shape until generation_runs gains a
    diversity_mode column (Phase 2 work). This contract is locked here so
    SI prompt construction can rely on the keys existing.
    """
    return {"contrarian": 0.0, "combinatoric": 0.0, "static": 0.0}


async def build_filter_summary(
    db: Database,
    *,
    top_concepts: int = 5,
    rate_threshold: float = 0.7,
    days: int = 30,
) -> dict:
    """Compose the filter_summary dict consumed by build_generation_prompt.

    Calls saturation_per_concept + filter_rate_by_category and shapes the
    result for direct injection into the prompt.

    Returns: {"saturated_concepts": list[str],
              "high_filter_rate_categories": list[tuple[str, float]]}
    """
    sat = await saturation_per_concept(db, days=days, top_n=top_concepts)
    rates = await filter_rate_by_category(db, days=min(days, 7))

    high = sorted(
        ((cat.value, r) for cat, r in rates.items() if r >= rate_threshold),
        key=lambda x: -x[1],
    )

    return {
        "saturated_concepts": [w for w, _ in sat],
        "high_filter_rate_categories": list(high),
    }


async def coverage_gaps(
    db: Database,
    threshold: int = 5,
) -> list[IdeaCategory]:
    """Return categories with active idea count below threshold.

    'Active' = status not in (rejected, archived). These are categories
    where generation should be nudged.
    """
    cursor = await db.db.execute(
        "SELECT category, COUNT(*) FROM ideas WHERE status NOT IN ('rejected', 'archived') GROUP BY category",
    )
    counts = {row[0]: row[1] for row in await cursor.fetchall()}

    gaps: list[IdeaCategory] = []
    for cat in IdeaCategory:
        if counts.get(cat.value, 0) < threshold:
            gaps.append(cat)
    return gaps
