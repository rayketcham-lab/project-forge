"""Tests for idea generator against the BYO-LLM backend."""

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from project_forge.engine.generator import IdeaGenerator
from project_forge.models import IdeaCategory

MOCK_IDEA_DATA = {
    "name": "Ghost Keys",
    "tagline": "Detect orphaned API keys across your entire infrastructure",
    "description": (
        "Ghost Keys scans your infrastructure for API keys that are still active "
        "but no longer used by any service. It integrates with cloud providers, "
        "secret managers, and application logs to build a dependency graph of key usage."
    ),
    "category": "security-tool",
    "market_analysis": (
        "API key sprawl is a growing problem as organizations adopt more SaaS tools. "
        "Existing secret scanners find exposed keys but don't track usage."
    ),
    "feasibility_score": 0.82,
    "mvp_scope": (
        "CLI tool that scans AWS IAM, GitHub tokens, and common secret managers. "
        "Reports unused keys older than 30 days."
    ),
    "tech_stack": ["python", "boto3", "click", "sqlite"],
}

MOCK_RESPONSE_JSON = json.dumps(MOCK_IDEA_DATA)


def _make_backend(text: str, *, name: str = "fake-backend") -> MagicMock:
    backend = MagicMock()
    backend.name = name
    backend.call.return_value = text
    return backend


def _asyncio_run(coro):
    return asyncio.run(coro)


class TestIdeaGenerator:
    @pytest.mark.asyncio
    async def test_generate_idea(self):
        backend = _make_backend(MOCK_RESPONSE_JSON)
        gen = IdeaGenerator(backend=backend)

        idea = await gen.generate(category=IdeaCategory.SECURITY_TOOL)

        assert idea.name == "Ghost Keys"
        assert idea.category == IdeaCategory.SECURITY_TOOL
        assert idea.feasibility_score == 0.82
        assert "python" in idea.tech_stack
        assert idea.status == "new"

    @pytest.mark.asyncio
    async def test_generate_handles_markdown_code_block(self):
        backend = _make_backend(f"Here's the idea:\n```json\n{MOCK_RESPONSE_JSON}\n```\n")
        gen = IdeaGenerator(backend=backend)
        idea = await gen.generate(category=IdeaCategory.SECURITY_TOOL)
        assert idea.name == "Ghost Keys"

    @pytest.mark.asyncio
    async def test_generate_clamps_score(self):
        data = dict(MOCK_IDEA_DATA)
        data["feasibility_score"] = 1.5
        backend = _make_backend(json.dumps(data))
        gen = IdeaGenerator(backend=backend)
        idea = await gen.generate(category=IdeaCategory.SECURITY_TOOL)
        assert idea.feasibility_score == 1.0

    @pytest.mark.asyncio
    async def test_generate_with_recent_ideas(self):
        backend = _make_backend(MOCK_RESPONSE_JSON)
        gen = IdeaGenerator(backend=backend)
        idea = await gen.generate(
            category=IdeaCategory.SECURITY_TOOL,
            recent_ideas=["Previous Idea 1", "Previous Idea 2"],
        )
        assert idea.name == "Ghost Keys"
        prompt = backend.call.call_args[0][0]
        assert "Previous Idea 1" in prompt
