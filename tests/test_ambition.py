"""Tests for ambition scoring — fix #v0.15.

ambition_score asks "how far does this push Claude / agent capability".
Distinct axis from fundability (can we sell it) and feasibility (can we
build it). Powers the /claude-lab page sort.

Heuristic signals:
  - category bonus (CLAUDE_SKILLS_AGENTS, AI_MARKETPLACE)
  - frontier keywords in description / mvp (mcp, sub-agent, attribution,
    marketplace, registry, fanned-out, reproducibility, provenance)
  - tech stack hints at the Anthropic / MCP ecosystem
  - description length proxy for substance
LLM tie-break in [0.40, 0.75] band when a backend is reachable.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database


def _idea(**overrides) -> Idea:
    base = dict(
        name="Generic Tool",
        tagline="t" * 20,
        description="A tool that does something useful.",
        category=IdeaCategory.OBSERVABILITY,
        market_analysis="Engineers like tools.",
        feasibility_score=0.7,
        mvp_scope="Phase 1: build it.",
        tech_stack=["python"],
    )
    base.update(overrides)
    return Idea(**base)


@pytest_asyncio.fixture
async def db(tmp_path):
    d = Database(tmp_path / "ambition.db")
    await d.connect()
    yield d
    await d.close()


class TestHeuristic:
    def test_baseline_is_modest(self):
        from project_forge.engine.ambition import score_ambition_heuristic

        s = score_ambition_heuristic(_idea())
        assert 0.0 <= s <= 0.4

    def test_claude_category_bumps_score(self):
        from project_forge.engine.ambition import score_ambition_heuristic

        a = score_ambition_heuristic(_idea())
        b = score_ambition_heuristic(_idea(category=IdeaCategory.CLAUDE_SKILLS_AGENTS))
        assert b > a

    def test_marketplace_category_bumps_score(self):
        from project_forge.engine.ambition import score_ambition_heuristic

        a = score_ambition_heuristic(_idea())
        b = score_ambition_heuristic(_idea(category=IdeaCategory.AI_MARKETPLACE))
        assert b > a

    def test_frontier_keywords_add_signal(self):
        from project_forge.engine.ambition import score_ambition_heuristic

        plain = _idea()
        loaded = _idea(
            description=(
                "A reproducibility ledger that records every sub-agent run "
                "with full provenance and attribution back to the original "
                "skill author across an open marketplace registry."
            ),
            mvp_scope="Phase 1: MCP server. Phase 2: attribution chain.",
        )
        assert score_ambition_heuristic(loaded) > score_ambition_heuristic(plain)

    def test_anthropic_stack_adds_signal(self):
        from project_forge.engine.ambition import score_ambition_heuristic

        a = score_ambition_heuristic(_idea())
        b = score_ambition_heuristic(_idea(tech_stack=["python", "anthropic", "mcp"]))
        assert b > a

    def test_clamped_to_unit_interval(self):
        from project_forge.engine.ambition import score_ambition_heuristic

        idea = _idea(
            category=IdeaCategory.CLAUDE_SKILLS_AGENTS,
            description=(
                "fanned-out sub-agent registry marketplace mcp provenance "
                "attribution reproducibility skills agents skills agents"
            ),
            mvp_scope="MCP server marketplace registry attribution",
            tech_stack=["python", "anthropic", "mcp", "anthropic-rs"],
        )
        s = score_ambition_heuristic(idea)
        assert 0.0 <= s <= 1.0


class TestLLMRefine:
    @pytest.mark.asyncio
    async def test_borderline_calls_llm(self, db, monkeypatch):
        from project_forge.engine import ambition

        backend = MagicMock()
        backend.name = "stub"
        backend.call = MagicMock(return_value='{"score": 0.81}')
        monkeypatch.setattr(ambition, "resolve_cheap_backend", lambda: backend)

        # Borderline — Claude category + a couple of frontier hints,
        # heuristic should land somewhere in the LLM-verify band.
        idea = _idea(
            category=IdeaCategory.CLAUDE_SKILLS_AGENTS,
            description="A sub-agent that runs MCP queries.",
            tech_stack=["python", "anthropic"],
        )
        s = await ambition.score_ambition(idea)
        assert abs(s - 0.81) < 0.05
        backend.call.assert_called_once()

    @pytest.mark.asyncio
    async def test_clearly_low_skips_llm(self, db, monkeypatch):
        from project_forge.engine import ambition

        backend = MagicMock()
        backend.call = MagicMock(return_value='{"score": 0.99}')
        monkeypatch.setattr(ambition, "resolve_cheap_backend", lambda: backend)

        # Generic devops tool, no frontier hints, no Claude category —
        # heuristic decides on its own.
        s = await ambition.score_ambition(_idea())
        assert s < 0.40
        backend.call.assert_not_called()


class TestBulkScoring:
    @pytest.mark.asyncio
    async def test_scores_only_unscored(self, db):
        from project_forge.engine.ambition import score_pending_ambition

        already = _idea(name="Already")
        already.ambition_score = 0.42
        await db.save_idea(already)

        for n in ("Fresh A", "Fresh B"):
            await db.save_idea(_idea(name=n))

        report = await score_pending_ambition(db, limit=10)
        assert report["scored"] == 2

        loaded = await db.get_idea(already.id)
        assert loaded.ambition_score == 0.42

    @pytest.mark.asyncio
    async def test_respects_limit(self, db):
        from project_forge.engine.ambition import score_pending_ambition

        for i in range(5):
            await db.save_idea(_idea(name=f"N{i}"))
        report = await score_pending_ambition(db, limit=3)
        assert report["scored"] == 3
