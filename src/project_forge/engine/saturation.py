"""Density loop (#97) — the single source of truth for corpus saturation.

The v0.17 Cartographer computed saturation and nothing consumed it; the
live generator was density-blind (recency-30 avoid list only) and the
live pair picker balanced exploration, not density. This module closes
the loop: everything that needs to know "how crowded is this zone" reads
it from here —

  - `category_density`    one aggregate, every category (Cartographer's
                          atlas now consumes this too)
  - `crowded_stems`       the wheel list: most-repeated tagline stems,
                          INCLUDING archived ideas so trims are remembered
  - `density_prompt_block` injected into the live generation prompt
  - `inverse_density_weight` / `pick_weighted_category`  churn auto-pick
  - `rank_pair_score`     density tie-break for the expand pair picker
                          (exploration count strictly dominates; density
                          only decides within a tier)
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Sequence
from typing import TYPE_CHECKING

from project_forge.models import IdeaCategory

if TYPE_CHECKING:
    from project_forge.storage.db import Database

# Categories with active ideas below this count are white space. These
# thresholds used to live in engine/cartographer.py; they moved here so
# the atlas and the generation loop can't disagree on what "saturated"
# means. Cartographer re-exports them for back-compat.
WHITE_SPACE_THRESHOLD: int = 5

# Categories with active ideas at or above this count are cluster-heavy.
SATURATION_COUNT_THRESHOLD: int = 20

# Density normalisation: ~this many active ideas halves a category's draw
# weight, and scales the pair-picker's density fraction.
DENSITY_SCALE: float = 40.0

# Trailing filler tokens stripped from tagline stems so "X auto-mapper for"
# and "X auto-mapper with" collapse to the same stem.
_TRAILING_STOP = frozenset(
    {"for", "with", "that", "and", "to", "of", "the", "a", "an", "in", "on", "across", "per", "via"}
)


async def category_density(db: Database) -> dict[str, int]:
    """Active idea count per category value, zero-filled for every
    IdeaCategory. One SQL aggregate — cheap enough to call per draw."""
    cursor = await db.db.execute(
        "SELECT category, COUNT(*) AS n FROM ideas WHERE status NOT IN ('rejected', 'archived') GROUP BY category"
    )
    rows = await cursor.fetchall()
    counts: dict[str, int] = {row[0]: int(row[1]) for row in rows}
    return {cat.value: counts.get(cat.value, 0) for cat in IdeaCategory}


def _tagline_stem(tagline: str) -> str | None:
    """First ~5 words of a tagline, trailing filler stripped. Two ideas
    that share a stem are re-treads of the same wheel even when their
    suffixes differ ('… for ops teams' vs '… for platform teams')."""
    words = (tagline or "").lower().split()
    stem = words[:5]
    while stem and stem[-1] in _TRAILING_STOP:
        stem.pop()
    if len(stem) < 3:
        return None
    return " ".join(stem)


async def crowded_stems(
    db: Database,
    category: IdeaCategory,
    *,
    min_count: int = 3,
    limit: int = 8,
) -> list[tuple[str, int]]:
    """The wheel list: tagline stems repeated >= min_count times in this
    category, most-repeated first. Deliberately includes ARCHIVED ideas
    (only rejected are excluded) so the density-thinning siphon doesn't
    erase the memory of what was already tried — trimmed wheels stay on
    the do-not-re-tread list."""
    cur = await db.db.execute(
        "SELECT tagline FROM ideas WHERE category = ? AND status != 'rejected'",
        (category.value,),
    )
    rows = await cur.fetchall()
    counter: Counter[str] = Counter()
    for r in rows:
        stem = _tagline_stem(r["tagline"])
        if stem:
            counter[stem] += 1
    return [(stem, n) for stem, n in counter.most_common(limit) if n >= min_count]


async def density_prompt_block(db: Database, category: IdeaCategory) -> str:
    """The saturation section for the live generation prompt: how crowded
    this category is, which concept stems are worn out, and — when the
    category is white space — an explicit flag-planting invitation."""
    density = await category_density(db)
    n = density.get(category.value, 0)

    lines = [f"## Corpus density for {category.value}"]
    if n >= SATURATION_COUNT_THRESHOLD:
        lines.append(
            f"{n} active ideas already exist in this category — CROWDED ZONE. "
            "The novelty bar is high: an idea adjacent to the existing pool "
            "will be rejected as a re-tread."
        )
    elif n < WHITE_SPACE_THRESHOLD:
        lines.append(
            f"Only {n} active ideas exist in this category — white space. "
            "Plant a flag: pitch what could become the defining idea of the "
            "category, not a timid variant."
        )
    else:
        lines.append(f"{n} active ideas exist in this category.")

    stems = await crowded_stems(db, category)
    if stems:
        lines.append("Well-trodden concept stems (do NOT re-tread these):")
        lines.extend(f'- "{stem}" (x{count})' for stem, count in stems)
    return "\n".join(lines)


def inverse_density_weight(count: int, *, scale: float = DENSITY_SCALE) -> float:
    """Draw weight that decays with active density: 0 ideas -> 1.0,
    `scale` ideas -> 0.5, and monotonically down from there."""
    return 1.0 / (1.0 + max(0, count) / scale)


def rank_pair_score(explored_count: int, density_a: int, density_b: int) -> float:
    """Score for the expand pair picker — LOWER is better.

    The integer exploration count is the primary term; the density term is
    a strict fraction < 1.0, so it can only break ties WITHIN an
    exploration tier, never promote a dense never-explored pair above a
    thinner once-explored one. Exploration semantics stay intact; density
    decides among equals.
    """
    combined = max(0, density_a) + max(0, density_b)
    density_fraction = combined / (combined + 2.0 * DENSITY_SCALE)
    return explored_count + density_fraction


async def pick_weighted_category(
    db: Database,
    categories: Sequence[IdeaCategory],
    *,
    rng: random.Random | object = random,
) -> IdeaCategory:
    """Inverse-density weighted draw — replaces uniform random.choice in
    auto-pick paths so a 190-idea category stops out-drawing a 4-idea one."""
    cats = list(categories)
    if not cats:
        raise ValueError("pick_weighted_category needs at least one category")
    density = await category_density(db)
    weights = [inverse_density_weight(density.get(c.value, 0)) for c in cats]
    return rng.choices(cats, weights=weights, k=1)[0]
