"""Bugs surfaced by the Think Tank audit + fixed.

1. Phase-schema self-contradiction: the mvp_scope schema asks for "Phase 1/2/3"
   but the scorer + quality-review penalized/rejected exactly that.
2. Dead Pulse cadence: keyed on the global expand watermark, so it ~never fired.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "ttbug.db")
    await database.connect()
    yield database
    await database.close()


class TestPhaseSchemaNoContradiction:
    def test_phase_tokens_not_in_penalty_lists(self):
        from project_forge.engine.quality_review import _NEW_PROJECT_SIGNALS
        from project_forge.engine.scorer import _OVERAMBITION_SIGNALS

        for tok in ("phase 1", "phase 2", "phase 3", "phase 4"):
            assert tok not in _OVERAMBITION_SIGNALS
        assert "phase 1" not in _NEW_PROJECT_SIGNALS
        assert "phase 2" not in _NEW_PROJECT_SIGNALS

    def test_real_overscope_signals_retained(self):
        from project_forge.engine.scorer import _OVERAMBITION_SIGNALS

        assert "multi-tenant" in _OVERAMBITION_SIGNALS
        assert "enterprise sso" in _OVERAMBITION_SIGNALS

    def test_si_idea_with_phased_scope_not_rejected_for_phases(self):
        from project_forge.engine.quality_review import review_idea

        idea = Idea(
            name="Decompose routes module",
            tagline="split the oversized routes.py into focused route modules",
            description=(
                "routes.py in src/project_forge/web/ is very large. Extract the "
                "board routes and the api routes into separate modules to reduce "
                "cognitive load and make edits less risky."
            ),
            category=IdeaCategory.SELF_IMPROVEMENT,
            market_analysis="Internal quality improvement — reduces maintenance risk.",
            feasibility_score=0.7,
            mvp_scope="Phase 1: extract board routes. Phase 2: extract api routes. Phase 3: update imports.",
            tech_stack=["python"],
        )
        result = review_idea(idea)
        reasons = " ".join(getattr(result, "reasons", []) or []).lower()
        assert "phase" not in reasons  # must NOT be rejected for following the schema


class TestPulseWatermark:
    @pytest.mark.asyncio
    async def test_pulse_due_even_when_other_ideas_are_fresh(self, db):
        # A pile of brand-new NON-pulse ideas (what the expand cadence produces).
        for i in range(5):
            idea = Idea(
                name=f"expand idea {i}",
                tagline="t",
                description="d" * 40,
                category=IdeaCategory.SECURITY_TOOL,
                market_analysis="m",
                feasibility_score=0.6,
                mvp_scope="s",
                tech_stack=["python"],
                content_hash=f"e{i}",
            )
            idea.generation_mode = "novel"
            await db.save_idea(idea)

        from project_forge.web.lifespan_scheduler import seconds_until_next_pulse

        # No pulse idea exists -> pulse is DUE (0), regardless of fresh expand ideas.
        delay = await seconds_until_next_pulse(db, timedelta(hours=3))
        assert delay == 0

    @pytest.mark.asyncio
    async def test_pulse_blocked_after_a_recent_pulse_idea(self, db):
        idea = Idea(
            name="pulse idea",
            tagline="t",
            description="d" * 40,
            category=IdeaCategory.MICRO_SAAS,
            market_analysis="m",
            feasibility_score=0.6,
            mvp_scope="s",
            tech_stack=["python"],
            content_hash="p1",
            generated_at=datetime.now(UTC),
        )
        idea.generation_mode = "pulse"
        await db.save_idea(idea)

        from project_forge.web.lifespan_scheduler import seconds_until_next_pulse

        delay = await seconds_until_next_pulse(db, timedelta(hours=3))
        assert delay > 0  # a recent pulse idea blocks until the interval elapses
