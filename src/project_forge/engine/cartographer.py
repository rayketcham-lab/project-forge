"""Cartographer — autonomous strategy meta-analysis over the whole corpus.

Reads the idea database and produces a structured 'atlas' summarising:
  - white_space:           under-represented categories (opportunity space)
  - saturation:            over-represented categories (cooling off)
  - top_clusters:          the five most-populated active categories
  - vertical_coverage:     per-category active idea counts (full picture)
  - recommended_next_bet:  the single highest-priority white-space category

The analysis is purely heuristic — no LLM call required. It combines
a raw idea-count pass with the rejection-rate signal from
``engine.telemetry.filter_rate_by_category`` so that categories that
*look* active but whose ideas keep getting dedup'd also surface as
saturated.

Public API (both importable in isolation; injectable DB for tests):
  async build_atlas(db) -> dict
  format_memo(atlas: dict) -> str
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from project_forge.engine.telemetry import filter_rate_by_category
from project_forge.models import IdeaCategory

if TYPE_CHECKING:
    from project_forge.storage.db import Database

logger = logging.getLogger(__name__)

# --- Thresholds ------------------------------------------------------------ #

# v0.21 (#97): the count thresholds and the per-category density aggregate
# moved to engine/saturation.py — the shared source of truth the generation
# loop also consumes — and are re-exported here for back-compat.
from project_forge.engine.saturation import (  # noqa: E402
    SATURATION_COUNT_THRESHOLD,
    WHITE_SPACE_THRESHOLD,
    category_density,
)

# Categories whose 30-day rejection rate exceeds this fraction are also
# flagged saturated even if their raw count is modest (ideas keep getting
# dedup'd, a leading indicator of concept exhaustion).
SATURATION_RATE_THRESHOLD: float = 0.70

# How many top clusters to report.
TOP_CLUSTER_COUNT: int = 5

# Categories we deprioritise when picking the recommended next bet because
# they are meta / system categories rather than user-facing idea spaces.
_LOW_PRIORITY_FOR_RECOMMENDATION: frozenset[str] = frozenset(
    {
        IdeaCategory.SELF_IMPROVEMENT.value,
    }
)


# --- Core async function --------------------------------------------------- #


async def build_atlas(db: Database) -> dict[str, Any]:
    """Return a structured atlas of the corpus's strategic shape.

    Queries:
      1. Active idea counts per category (SQL aggregate, no full scan).
      2. 30-day rejection rates per category (via telemetry helper).

    Returns
    -------
    dict with keys:
      white_space       list[str]          -- category values under-covered
      saturation        list[str]          -- category values over-covered
      top_clusters      list[dict]         -- [{category, count}] sorted desc
      vertical_coverage dict[str, int]     -- every category -> active count
      recommended_next_bet str             -- highest-priority white space pick
      generated_at      str                -- ISO timestamp of this run
    """
    # 1. Per-category active idea counts — via the shared density source
    #    (#97) so the atlas and the generation loop can't drift.
    vertical_coverage: dict[str, int] = await category_density(db)

    # 2. Rejection rates from telemetry (30-day window, category-keyed).
    try:
        rejection_rates = await filter_rate_by_category(db, days=30)
    except Exception:
        logger.warning("filter_rate_by_category failed; continuing without rates")
        rejection_rates = {}

    # 3. White space: low active count across all known categories.
    white_space: list[str] = [
        cat.value for cat in IdeaCategory if vertical_coverage.get(cat.value, 0) < WHITE_SPACE_THRESHOLD
    ]

    # 4. Saturation: heavy count OR high rejection rate.
    saturation: list[str] = []
    saturated_set: set[str] = set()
    for cat in IdeaCategory:
        is_heavy = vertical_coverage.get(cat.value, 0) >= SATURATION_COUNT_THRESHOLD
        rate = rejection_rates.get(cat, 0.0)
        is_high_rejection = rate >= SATURATION_RATE_THRESHOLD
        if is_heavy or is_high_rejection:
            saturation.append(cat.value)
            saturated_set.add(cat.value)

    # 5. Top clusters (most-populated, excluding archived/rejected counts).
    top_clusters: list[dict[str, Any]] = sorted(
        [{"category": c, "count": n} for c, n in vertical_coverage.items() if n > 0],
        key=lambda x: (-x["count"], x["category"]),
    )[:TOP_CLUSTER_COUNT]

    # 6. Recommended next bet: largest white-space gap that isn't low-priority,
    #    and isn't already saturated (edge-case guard).  Favour categories that
    #    *have* some ideas (i.e. not completely empty) because they've already
    #    been seeded — they just need more density.  Fall back to the first
    #    empty white-space category, then to "balanced" if nothing qualifies.
    candidate_ws = [c for c in white_space if c not in _LOW_PRIORITY_FOR_RECOMMENDATION and c not in saturated_set]
    # Prefer categories with at least 1 idea (partially seeded)
    seeded = [c for c in candidate_ws if vertical_coverage.get(c, 0) >= 1]
    # Within each group sort by descending count (largest gap first)
    seeded.sort(key=lambda c: -vertical_coverage.get(c, 0))
    candidate_ws.sort(key=lambda c: -vertical_coverage.get(c, 0))

    if seeded:
        recommended_next_bet = seeded[0]
    elif candidate_ws:
        recommended_next_bet = candidate_ws[0]
    else:
        recommended_next_bet = "balanced"

    return {
        "white_space": white_space,
        "saturation": saturation,
        "top_clusters": top_clusters,
        "vertical_coverage": vertical_coverage,
        "recommended_next_bet": recommended_next_bet,
        "generated_at": datetime.now(UTC).isoformat(),
    }


# --- Memo formatter -------------------------------------------------------- #


def format_memo(atlas: dict[str, Any]) -> str:
    """Render the atlas as a markdown 'State of the Forge' memo.

    Deterministic and pure — no DB, no LLM. Takes the dict returned by
    ``build_atlas`` and formats it for display and logging.
    """
    lines: list[str] = []
    ts = atlas.get("generated_at", "unknown")
    lines.append(f"# State of the Forge\n\n_Generated: {ts}_\n")

    # White space
    white = atlas.get("white_space", [])
    if white:
        lines.append("## White Space (under-covered categories)")
        lines.append(
            "_These categories have fewer than the density threshold. Prime targets for the next generation run._\n"
        )
        for cat in white:
            coverage: dict[str, int] = atlas.get("vertical_coverage", {})
            count = coverage.get(cat, 0)
            lines.append(f"- **{cat}** ({count} active ideas)")
        lines.append("")
    else:
        lines.append("## White Space\n\n_All categories meet minimum density._\n")

    # Saturation
    saturated = atlas.get("saturation", [])
    if saturated:
        lines.append("## Saturation (over-covered / high-rejection categories)")
        lines.append(
            "_These categories have high idea density or elevated rejection "
            "rates — the engine should diversify away from them._\n"
        )
        for cat in saturated:
            coverage = atlas.get("vertical_coverage", {})
            count = coverage.get(cat, 0)
            lines.append(f"- **{cat}** ({count} active ideas)")
        lines.append("")
    else:
        lines.append("## Saturation\n\n_No categories flagged as saturated._\n")

    # Top clusters
    clusters = atlas.get("top_clusters", [])
    if clusters:
        lines.append("## Top Clusters (most populated)\n")
        for entry in clusters:
            lines.append(f"- **{entry['category']}**: {entry['count']} ideas")
        lines.append("")

    # Recommended next bet
    rec = atlas.get("recommended_next_bet", "balanced")
    lines.append("## Recommended Next Bet\n")
    if rec == "balanced":
        lines.append(
            "> **Balanced** — the corpus is well-distributed across all "
            "categories. Run the standard horizontal expansion cadence."
        )
    else:
        coverage = atlas.get("vertical_coverage", {})
        count = coverage.get(rec, 0)
        lines.append(
            f"> **{rec}** — only {count} active ideas. "
            "Prioritise this category in the next generation run to "
            "close the coverage gap."
        )
    lines.append("")

    return "\n".join(lines)
