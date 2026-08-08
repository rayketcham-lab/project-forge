"""Horizontal expansion — cross-category idea generation.

Generates ideas that deliberately bridge different categories,
targeting the least-explored category pairs to ensure systematic
coverage of all 66 possible intersections.
"""

import logging
import random

from project_forge.cron.auto_scan import generate_local_idea
from project_forge.engine.categories import CATEGORY_SEEDS
from project_forge.engine.dedup import filter_and_save
from project_forge.engine.llm_generator import generate_idea_llm
from project_forge.engine.quality_review import review_idea
from project_forge.engine.saturation import category_density, rank_pair_score
from project_forge.engine.super_ideas import SuperIdeaGenerator, pick_least_covered_slot
from project_forge.models import GATED_CATEGORIES, Idea, IdeaCategory
from project_forge.storage.db import Database

logger = logging.getLogger(__name__)


async def pick_cross_category_pair(
    db: Database,
    exclude: list[tuple[IdeaCategory, IdeaCategory]] | None = None,
) -> tuple[IdeaCategory, IdeaCategory]:
    """Pick the least-explored category pair for cross-pollination.

    Returns two different IdeaCategory values, targeting pairs that have
    been explored the fewest times. v0.21 (#97): within an exploration
    tier, corpus DENSITY breaks the tie — `rank_pair_score` keeps the
    integer explored-count as the dominant term and adds a strict-fraction
    density term, so a crowded pair can never outrank a thinner pair that
    has been explored less, but among equally-explored pairs the thinnest
    zones win. That's what points the hourly expand cadence at white space
    instead of re-treading 190-idea categories.
    """
    excluded_normalized = set()
    if exclude:
        for a, b in exclude:
            pair = tuple(sorted([a.value, b.value]))
            excluded_normalized.add(pair)

    pairs = await db.get_least_explored_pairs(limit=10_000)

    # Self-improvement ideas come ONLY from the introspection engine (concrete,
    # grounded code tasks). Never let cross-category generation bridge into it —
    # that produced garbled "Dashboard UX Improvements for Performance" noise.
    si = IdeaCategory.SELF_IMPROVEMENT.value
    density = await category_density(db)
    best: tuple[float, str, str] | None = None
    for cat_a_val, cat_b_val, count in pairs:
        if si in (cat_a_val, cat_b_val):
            continue
        if (cat_a_val, cat_b_val) in excluded_normalized:
            continue
        score = rank_pair_score(count, density.get(cat_a_val, 0), density.get(cat_b_val, 0))
        key = (score, cat_a_val, cat_b_val)  # stable, deterministic tiebreak
        if best is None or key < best:
            best = key
    if best is not None:
        return IdeaCategory(best[1]), IdeaCategory(best[2])

    # Fallback: random pair (should never reach here with 600+ pairs)
    cats = [c for c in IdeaCategory if c != IdeaCategory.SELF_IMPROVEMENT and c not in GATED_CATEGORIES]
    a, b = random.sample(cats, 2)
    return a, b


async def generate_cross_idea(
    db: Database,
    primary_cat: IdeaCategory,
    secondary_cat: IdeaCategory,
) -> Idea:
    """Generate an idea that bridges two categories.

    LLM-first when a cheap backend resolves — each call rotates through
    persona + 1-of-5 generation modes + anti-similarity injection, so
    new ideas don't look like remixes of old ones. Falls back to the
    template generator on no-backend / parse-failure so the cycle never
    stalls.
    """
    llm_result = await generate_idea_llm(db, primary_cat)
    if llm_result is not None:
        idea = llm_result.idea
        # Note the cross-category bridge inline so the routing layer's
        # category-pair telemetry still has something to work with.
        idea.description = (
            idea.description + "\n\n**Cross-category bridge:** "
            f"primary {primary_cat.value} × secondary {secondary_cat.value} "
            f"(LLM mode: {llm_result.mode})."
        )
        return idea

    recent_names = list(set(await db.get_all_idea_names()))

    # Generate from primary category — generate_local_idea picks a random
    # direction internally (basic/contrarian/combinatoric/crossover).
    # We then explicitly enrich with cross-category content below.
    idea, cat_val, c_idx, d_idx, actual_direction = generate_local_idea(
        category=primary_cat,
        recent_names=recent_names,
    )

    # Enrich the idea with explicit cross-category reference
    secondary_seeds = CATEGORY_SEEDS[secondary_cat]
    secondary_concept = random.choice(secondary_seeds["seed_concepts"])
    secondary_domain = random.choice(secondary_seeds["domains_to_cross"])
    concept_short = secondary_concept.split("—")[0].split("(")[0].strip()

    cross_note = (
        f"\n\n**Cross-category bridge:** This idea connects {primary_cat.value} "
        f"with {secondary_cat.value} — specifically, how {concept_short} "
        f"intersects with {secondary_domain}."
    )
    idea.description += cross_note

    # Record the tuple for dedup
    await db.record_used_tuple(cat_val, c_idx, d_idx, actual_direction)

    return idea


async def run_horizontal_cycle(db: Database) -> list[Idea]:
    """Run one horizontal expansion cycle, producing exactly 2 ideas.

    Idea 1: Cross-category atomic idea targeting the least-explored pair.
    Idea 2: Super idea synthesis using hour-rotated category lens.
    """
    ideas: list[Idea] = []

    # --- Idea 1: Cross-category idea ---
    cat_a, cat_b = await pick_cross_category_pair(db)
    idea1 = await generate_cross_idea(db, cat_a, cat_b)
    review = review_idea(idea1)
    if not review.passed:
        logger.warning("Cross-idea '%s' rejected by quality review: %s", idea1.name, "; ".join(review.reasons))
    else:
        await filter_and_save(idea1, db)
    await db.record_category_pair(cat_a.value, cat_b.value, idea1.id)
    ideas.append(idea1)
    logger.info(
        "Cross-category: %s (%s x %s, %.2f)",
        idea1.name,
        cat_a.value,
        cat_b.value,
        idea1.feasibility_score,
    )

    # --- Idea 2: Super idea synthesis ---
    # Coverage-aware rotation: pick the slot whose seed_categories carry
    # the fewest active supers. Replaces `hour % 5` (May 2026 corpus audit
    # showed the mechanical rotation let top 6 themes accrue ~20% of all
    # supers).
    slot = await pick_least_covered_slot(db)
    sig = SuperIdeaGenerator(db)
    super_idea = await sig.generate_seeded(slot=slot)

    if super_idea:
        # generate_seeded already stores it; retrieve the stored Idea
        stored = await db.get_idea(super_idea.id)
        if stored:
            ideas.append(stored)
            # Record the category pairs spanned by this super idea
            cats = super_idea.categories_spanned
            for i, c in enumerate(cats):
                for other in cats[i + 1 :]:
                    await db.record_category_pair(c.value, other.value, super_idea.id)
            logger.info(
                "Super idea: %s (impact: %.2f, %d components)",
                super_idea.name,
                super_idea.impact_score,
                len(super_idea.component_idea_ids),
            )

    if len(ideas) < 2:
        # Fallback: generate a second cross-category idea with a different pair
        cat_c, cat_d = await pick_cross_category_pair(db, exclude=[(cat_a, cat_b)])
        idea2 = await generate_cross_idea(db, cat_c, cat_d)
        await filter_and_save(idea2, db)
        await db.record_category_pair(cat_c.value, cat_d.value, idea2.id)
        ideas.append(idea2)
        logger.info(
            "Cross-category fallback: %s (%s x %s, %.2f)",
            idea2.name,
            cat_c.value,
            cat_d.value,
            idea2.feasibility_score,
        )

    return ideas
