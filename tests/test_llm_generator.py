"""Tests for the LLM-first idea generator.

The pivot: stop slot-filling templates (the v0.11 "drum-drum-drum" failure
mode) and produce genuinely-new ideas by:
  - calling Haiku 4.5 per idea (cheap, ~$0.0024/call)
  - rotating through 10+ category-specific personas
  - injecting 30 recent idea names as "do NOT produce anything like these"
  - picking from 5 generation MODES (novel / inversion / bundle /
    microservice / adversarial) so the shape of ideas varies, not just
    the topics

This module is the LLM-first entry point. The horizontal cycle prefers
it over the template generator when a backend resolves; the template
generator stays as the no-backend fallback.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from project_forge.models import IdeaCategory
from project_forge.storage.db import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "llmgen.db")
    await database.connect()
    yield database
    await database.close()


def _stub_backend(payload: dict, name: str = "stub:haiku") -> MagicMock:
    """Build a fake LLMBackend that returns `payload` as JSON for every call."""
    backend = MagicMock()
    backend.name = name
    backend.call = MagicMock(return_value=json.dumps(payload))
    return backend


_OK_PAYLOAD = {
    "name": "Newsletter Pricing Optimizer",
    "tagline": "subscription pricing A/B testing for indie newsletter operators",
    "description": (
        "A self-serve tool that runs paid-tier price experiments for "
        "independent newsletter operators. Picks split traffic, measures "
        "churn lift, and surfaces the revenue-maximising price within 4 weeks."
    ),
    "market_analysis": (
        "Newsletter monetization is hot but most operators guess at pricing. "
        "Even a $2 price delta moves MRR meaningfully at 10k+ subscribers."
    ),
    "mvp_scope": (
        "Phase 1: integrate Stripe + Substack-API. "
        "Phase 2: cohort price-split assignment. "
        "Phase 3: weekly delta report email."
    ),
    "tech_stack": ["python", "fastapi", "stripe", "supabase"],
    "feasibility_score": 0.86,
    "mode_rationale": "Indie hackers feel newsletter monetization pain acutely.",
}


# --------------------------------------------------------------------------- #
# Backend absence                                                             #
# --------------------------------------------------------------------------- #


class TestNoBackend:
    @pytest.mark.asyncio
    async def test_returns_none_when_resolver_finds_no_backend(self, db, monkeypatch):
        """When neither API key nor `claude` CLI is reachable, generator
        must return None so the caller can fall back to the template path.
        Passing backend=None to the function triggers the resolver path —
        we patch that path so this test runs deterministically even on a
        host where Haiku via CLI happens to be available."""
        from project_forge.engine import llm_generator

        monkeypatch.setattr(
            llm_generator, "resolve_cheap_backend", lambda: None,
        )
        result = await llm_generator.generate_idea_llm(
            db, IdeaCategory.AUTOMATION_INCOME, backend=None,
        )
        assert result is None


# --------------------------------------------------------------------------- #
# Mode selection                                                              #
# --------------------------------------------------------------------------- #


class TestModes:
    @pytest.mark.asyncio
    async def test_uses_specified_mode(self, db):
        from project_forge.engine.llm_generator import GENERATION_MODES, generate_idea_llm

        backend = _stub_backend(_OK_PAYLOAD)
        for mode in GENERATION_MODES:
            result = await generate_idea_llm(
                db,
                IdeaCategory.AUTOMATION_INCOME,
                mode=mode,
                backend=backend,
            )
            assert result is not None
            assert result.mode == mode

    @pytest.mark.asyncio
    async def test_picks_least_used_mode_when_unspecified(self, db, monkeypatch):
        """If mode isn't passed, the picker should choose the mode that
        has the fewest active ideas in this category."""
        from project_forge.engine.llm_generator import pick_least_used_mode

        # Record three ideas with mode='novel' so any other mode is fresher.
        from project_forge.models import Idea

        for i in range(3):
            idea = Idea(
                name=f"Already novel {i}",
                tagline="t",
                description="d",
                category=IdeaCategory.AUTOMATION_INCOME,
                market_analysis="m",
                feasibility_score=0.7,
                mvp_scope="mvp",
                tech_stack=["python"],
                generation_mode="novel",
            )
            await db.save_idea(idea)

        picked = await pick_least_used_mode(db, IdeaCategory.AUTOMATION_INCOME)
        assert picked != "novel"


# --------------------------------------------------------------------------- #
# Anti-similarity injection                                                   #
# --------------------------------------------------------------------------- #


class TestAntiSimilarity:
    @pytest.mark.asyncio
    async def test_prompt_includes_recent_names(self, db):
        from project_forge.engine.llm_generator import generate_idea_llm
        from project_forge.models import Idea

        for n in ("Subscriber Churn Predictor", "Open-Rate Forecaster"):
            await db.save_idea(Idea(
                name=n,
                tagline="t",
                description="d",
                category=IdeaCategory.AUTOMATION_INCOME,
                market_analysis="m",
                feasibility_score=0.7,
                mvp_scope="mvp",
                tech_stack=["python"],
            ))

        backend = _stub_backend(_OK_PAYLOAD)
        await generate_idea_llm(
            db,
            IdeaCategory.AUTOMATION_INCOME,
            mode="novel",
            backend=backend,
        )
        # The prompt fed to the backend must include the recent names so
        # the LLM can avoid them.
        sent = backend.call.call_args.args[0]
        assert "Subscriber Churn Predictor" in sent
        assert "Open-Rate Forecaster" in sent
        # And the explicit anti-similarity instruction.
        assert "do not produce" in sent.lower() or "avoid" in sent.lower()


# --------------------------------------------------------------------------- #
# Persona rotation                                                            #
# --------------------------------------------------------------------------- #


class TestPersonas:
    @pytest.mark.asyncio
    async def test_persona_is_category_specific(self, db):
        """Money-bot category should use indie-hacker-style personas, not CISOs."""
        from project_forge.engine.llm_generator import generate_idea_llm

        backend = _stub_backend(_OK_PAYLOAD)
        result = await generate_idea_llm(
            db,
            IdeaCategory.AUTOMATION_INCOME,
            mode="novel",
            backend=backend,
        )
        assert result is not None
        # Persona is one of the AUTOMATION_INCOME personas, not a security one.
        # (CISO / PKI personas would be wildly wrong for this category.)
        lowered = result.persona.lower()
        assert "ciso" not in lowered
        assert "pki" not in lowered

    @pytest.mark.asyncio
    async def test_security_category_uses_security_persona(self, db):
        from project_forge.engine.llm_generator import generate_idea_llm

        backend = _stub_backend(_OK_PAYLOAD)
        result = await generate_idea_llm(
            db,
            IdeaCategory.SECURITY_TOOL,
            mode="novel",
            backend=backend,
        )
        assert result is not None
        # Some signal that we're in the security idiom — at minimum, the
        # persona should not be one of the consumer-app personas.
        lowered = result.persona.lower()
        assert "parent" not in lowered
        assert "tiktok" not in lowered


# --------------------------------------------------------------------------- #
# Parsing                                                                     #
# --------------------------------------------------------------------------- #


class TestParsing:
    @pytest.mark.asyncio
    async def test_returns_none_on_malformed_json(self, db):
        from project_forge.engine.llm_generator import generate_idea_llm

        backend = MagicMock()
        backend.name = "stub"
        backend.call = MagicMock(return_value="not json at all !!")
        result = await generate_idea_llm(
            db, IdeaCategory.AUTOMATION_INCOME, mode="novel", backend=backend,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_strips_markdown_codefence(self, db):
        from project_forge.engine.llm_generator import generate_idea_llm

        backend = MagicMock()
        backend.name = "stub"
        wrapped = f"```json\n{json.dumps(_OK_PAYLOAD)}\n```"
        backend.call = MagicMock(return_value=wrapped)
        result = await generate_idea_llm(
            db, IdeaCategory.AUTOMATION_INCOME, mode="novel", backend=backend,
        )
        assert result is not None
        assert result.idea.name == _OK_PAYLOAD["name"]

    @pytest.mark.asyncio
    async def test_records_mode_on_idea(self, db):
        from project_forge.engine.llm_generator import generate_idea_llm

        backend = _stub_backend(_OK_PAYLOAD)
        result = await generate_idea_llm(
            db, IdeaCategory.AUTOMATION_INCOME, mode="inversion", backend=backend,
        )
        assert result is not None
        assert result.idea.generation_mode == "inversion"
