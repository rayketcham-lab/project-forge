"""Tests for fundability scoring.

`fundability_score` is the engine's bet on monetization viability — distinct
from `feasibility_score` (can we build it). Used by the auto-promotion loop
(future) to bias toward ideas that can actually flip money.

Heuristic factors:
  - tech_stack hints at payments (stripe, paddle, lemonsqueezy)        +0.15
  - mvp_scope/description mentions paid/subscription/SaaS/recurring    +0.15
  - market_analysis names a specific buyer with budget                 +0.15
  - category is monetization-friendly (AUTOMATION_INCOME +0.20, etc.)  varies
  - description hints at recurring revenue                             +0.10
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "fund.db")
    await database.connect()
    yield database
    await database.close()


def _idea(**overrides) -> Idea:
    base = dict(
        name="Generic Tool",
        tagline="generic tool tagline",
        description="A tool that does something useful.",
        category=IdeaCategory.OBSERVABILITY,
        market_analysis="Engineers like tools.",
        feasibility_score=0.7,
        mvp_scope="Phase 1: build it.",
        tech_stack=["python"],
    )
    base.update(overrides)
    return Idea(**base)


class TestHeuristicScore:
    def test_baseline_score_is_low_for_generic_idea(self):
        from project_forge.engine.fundability import score_fundability_heuristic

        s = score_fundability_heuristic(_idea())
        assert 0.0 <= s <= 0.5

    def test_payment_tech_bumps_score(self):
        from project_forge.engine.fundability import score_fundability_heuristic

        without = score_fundability_heuristic(_idea())
        with_pay = score_fundability_heuristic(
            _idea(
                tech_stack=["python", "fastapi", "stripe"],
            )
        )
        assert with_pay > without

    def test_paid_keywords_bump_score(self):
        from project_forge.engine.fundability import score_fundability_heuristic

        idea = _idea(
            description="A SaaS tool with subscription billing for indie founders.",
            mvp_scope="Phase 1: Stripe integration. Phase 2: paid tier.",
        )
        s = score_fundability_heuristic(idea)
        assert s >= 0.4

    def test_automation_income_category_bonus(self):
        from project_forge.engine.fundability import score_fundability_heuristic

        baseline = score_fundability_heuristic(_idea())
        money_cat = score_fundability_heuristic(
            _idea(
                category=IdeaCategory.AUTOMATION_INCOME,
            )
        )
        assert money_cat > baseline

    def test_score_clamped_to_unit_interval(self):
        from project_forge.engine.fundability import score_fundability_heuristic

        # Pile on every bonus.
        idea = _idea(
            category=IdeaCategory.AUTOMATION_INCOME,
            description="SaaS subscription paid recurring revenue monetization.",
            mvp_scope="Phase 1: Stripe paid tier subscription billing.",
            tech_stack=["python", "fastapi", "stripe", "paddle"],
            market_analysis="Indie hackers paying for newsletter tools at $99/mo.",
        )
        s = score_fundability_heuristic(idea)
        assert 0.0 <= s <= 1.0


class TestBulkScoring:
    @pytest.mark.asyncio
    async def test_scores_unscored_ideas_only(self, db):
        from project_forge.engine.fundability import score_pending_ideas

        # Pre-existing scored idea — shouldn't be touched.
        already = _idea(name="Pre-scored")
        already.fundability_score = 0.42
        await db.save_idea(already)

        # Unscored ideas.
        for n in ("New A", "New B"):
            await db.save_idea(_idea(name=n))

        report = await score_pending_ideas(db, limit=10)
        assert report["scored"] == 2

        # The already-scored one keeps its score.
        loaded = await db.get_idea(already.id)
        assert loaded.fundability_score == 0.42

    @pytest.mark.asyncio
    async def test_persists_score_to_db(self, db):
        from project_forge.engine.fundability import score_pending_ideas

        idea = _idea(name="Stripe SaaS", tech_stack=["python", "stripe"])
        await db.save_idea(idea)

        await score_pending_ideas(db, limit=10)
        loaded = await db.get_idea(idea.id)
        assert loaded.fundability_score is not None
        assert 0.0 <= loaded.fundability_score <= 1.0

    @pytest.mark.asyncio
    async def test_respects_limit(self, db):
        from project_forge.engine.fundability import score_pending_ideas

        for i in range(5):
            await db.save_idea(_idea(name=f"Unscored {i}"))

        report = await score_pending_ideas(db, limit=2)
        assert report["scored"] == 2


class TestLLMVerification:
    @pytest.mark.asyncio
    async def test_llm_pulls_borderline_to_resolved_score(self, db, monkeypatch):
        """When heuristic lands in the borderline band, the cheap LLM is
        asked for a finer score."""
        from project_forge.engine import fundability

        backend = MagicMock()
        backend.name = "stub"
        backend.call = MagicMock(return_value='{"score": 0.78}')
        monkeypatch.setattr(
            fundability,
            "resolve_cheap_backend",
            lambda: backend,
        )

        # Heuristic gives ~0.55 (paid keywords + payment stack, no extra
        # category bonus) — in the borderline band.
        idea = _idea(
            tech_stack=["python", "stripe"],
            description="A subscription tool for indie founders.",
            mvp_scope="Phase 1: paid plan with Stripe billing.",
        )
        score = await fundability.score_fundability(idea)
        assert abs(score - 0.78) < 0.05
        backend.call.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_llm_for_clear_low_score(self, db, monkeypatch):
        from project_forge.engine import fundability

        backend = MagicMock()
        backend.call = MagicMock(return_value='{"score": 0.99}')
        monkeypatch.setattr(
            fundability,
            "resolve_cheap_backend",
            lambda: backend,
        )

        # Generic dev tool, no paid signals — heuristic clearly low,
        # should skip the LLM verification entirely.
        idea = _idea()
        score = await fundability.score_fundability(idea)
        assert score < 0.4
        backend.call.assert_not_called()
