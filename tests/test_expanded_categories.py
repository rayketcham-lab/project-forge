"""Tests for the v0.16 category expansion.

Adds 4 new money-bot categories and 4 new Claude Lab categories. These
tests pin down the wiring that makes a category actually show up on its
themed dashboard and score high enough to surface:

  1. enum membership
  2. canonical grouping tuples (MONEY_CATEGORIES / CLAUDE_LAB_CATEGORIES)
  3. CATEGORY_SEEDS, personas, and tech stacks
  4. artifact-type rotation for the Claude Lab categories
  5. scoring bonuses (fundability for money, ambition for Claude Lab)
  6. the route-level tuples + stats counter derive from the canonical lists
"""

from __future__ import annotations

from project_forge.engine.ambition import _CATEGORY_BONUS as AMBITION_BONUS
from project_forge.engine.categories import CATEGORY_SEEDS
from project_forge.engine.fundability import _CATEGORY_BONUS as FUNDABILITY_BONUS
from project_forge.engine.llm_generator import (
    _ARTIFACT_ROTATION_CATEGORIES,
    PERSONAS_BY_CATEGORY,
)
from project_forge.models import (
    CLAUDE_LAB_CATEGORIES,
    MONEY_CATEGORIES,
    IdeaCategory,
)

NEW_MONEY = (
    IdeaCategory.MICRO_SAAS,
    IdeaCategory.VERTICAL_SAAS,
    IdeaCategory.ECOMMERCE_TOOLS,
    IdeaCategory.FINTECH_TOOLS,
)

NEW_CLAUDE_LAB = (
    IdeaCategory.AGENT_INFRA,
    IdeaCategory.CLAUDE_EVALS,
    IdeaCategory.AGENT_SECURITY,
    IdeaCategory.CONTEXT_MEMORY,
)


class TestEnumAndGroupings:
    def test_new_money_categories_in_enum(self):
        for cat in NEW_MONEY:
            assert isinstance(cat, IdeaCategory)

    def test_new_claude_lab_categories_in_enum(self):
        for cat in NEW_CLAUDE_LAB:
            assert isinstance(cat, IdeaCategory)

    def test_new_money_categories_are_grouped_as_money(self):
        for cat in NEW_MONEY:
            assert cat in MONEY_CATEGORIES

    def test_new_claude_lab_categories_are_grouped_as_claude_lab(self):
        for cat in NEW_CLAUDE_LAB:
            assert cat in CLAUDE_LAB_CATEGORIES

    def test_groupings_are_disjoint(self):
        assert not (set(MONEY_CATEGORIES) & set(CLAUDE_LAB_CATEGORIES))

    def test_existing_money_categories_retained(self):
        for cat in (
            IdeaCategory.AUTOMATION_INCOME,
            IdeaCategory.CREATOR_TOOLS,
            IdeaCategory.CONSUMER_APP,
            IdeaCategory.PRODUCTIVITY,
        ):
            assert cat in MONEY_CATEGORIES


class TestSeedsAndPersonas:
    def test_new_categories_have_well_formed_seeds(self):
        for cat in NEW_MONEY + NEW_CLAUDE_LAB:
            assert cat in CATEGORY_SEEDS, f"missing seeds for {cat}"
            seeds = CATEGORY_SEEDS[cat]
            assert len(seeds["seed_concepts"]) >= 5
            assert len(seeds["domains_to_cross"]) >= 3
            # No duplicate concepts.
            assert len(seeds["seed_concepts"]) == len(set(seeds["seed_concepts"]))

    def test_new_categories_have_personas(self):
        for cat in NEW_MONEY + NEW_CLAUDE_LAB:
            assert cat in PERSONAS_BY_CATEGORY, f"missing personas for {cat}"
            assert len(PERSONAS_BY_CATEGORY[cat]) >= 5


class TestArtifactRotation:
    def test_claude_lab_categories_rotate_artifacts(self):
        # Every Claude Lab category — old and new — rotates artifact shapes.
        for cat in CLAUDE_LAB_CATEGORIES:
            assert cat in _ARTIFACT_ROTATION_CATEGORIES

    def test_money_categories_do_not_rotate_artifacts(self):
        for cat in MONEY_CATEGORIES:
            assert cat not in _ARTIFACT_ROTATION_CATEGORIES


class TestScoringBonuses:
    def test_new_money_categories_have_fundability_bonus(self):
        for cat in NEW_MONEY:
            assert FUNDABILITY_BONUS.get(cat, 0.0) > 0.0

    def test_new_claude_lab_categories_have_ambition_bonus(self):
        for cat in NEW_CLAUDE_LAB:
            assert AMBITION_BONUS.get(cat, 0.0) > 0.0


class TestRouteAndStatsWiring:
    # Import app before routes in each test: importing routes cold trips the
    # pre-existing app<->routes circular import when this file runs in
    # isolation (the full suite masks it via import-order luck). Same
    # pattern as test_crypto.py / test_cashflow.py.

    def test_routes_money_tuple_matches_canonical(self):
        from project_forge.web import app as _app  # noqa: F401
        from project_forge.web import routes

        assert set(routes._MONEY_CATEGORIES) == {c.value for c in MONEY_CATEGORIES}

    def test_routes_claude_lab_tuple_matches_canonical(self):
        from project_forge.web import app as _app  # noqa: F401
        from project_forge.web import routes

        assert set(routes._CLAUDE_LAB_CATEGORIES) == {c.value for c in CLAUDE_LAB_CATEGORIES}
