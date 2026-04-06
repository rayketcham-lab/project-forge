"""Tests for static introspection — Forge Lab proposals WITHOUT API key (#45).

The introspection engine must generate self-improvement proposals by analyzing the
codebase locally, without requiring an Anthropic API key.

Covers:
1. Static analyzer detects untested source modules
2. Static analyzer detects large files needing decomposition
3. Static analyzer generates Idea objects from findings
4. introspect_runner works without API key (static fallback)
5. Forge Lab shows static proposals
6. generator.generate() accepts prompt_override parameter
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.models import Idea, IdeaCategory
from project_forge.web.app import app, db

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest_asyncio.fixture
async def client(tmp_path):
    db.db_path = tmp_path / "test_static_introspect.db"
    await db.connect()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await db.close()


# ---------------------------------------------------------------------------
# 1. Static analyzer detects untested modules
# ---------------------------------------------------------------------------


class TestStaticAnalyzerUntested:
    """Static analyzer should detect source modules without test files."""

    def test_finds_untested_modules(self):
        from project_forge.engine.static_introspect import find_untested_modules

        findings = find_untested_modules(PROJECT_ROOT)
        assert isinstance(findings, list)
        # Each finding should have module name and path
        for f in findings:
            assert "module" in f
            assert "path" in f

    def test_excludes_init_files(self):
        """__init__.py files should not be flagged as untested."""
        from project_forge.engine.static_introspect import find_untested_modules

        findings = find_untested_modules(PROJECT_ROOT)
        modules = [f["module"] for f in findings]
        assert "__init__" not in modules


# ---------------------------------------------------------------------------
# 2. Static analyzer detects large files
# ---------------------------------------------------------------------------


class TestStaticAnalyzerLargeFiles:
    """Static analyzer should detect files needing decomposition."""

    def test_finds_large_files(self):
        from project_forge.engine.static_introspect import find_large_files

        findings = find_large_files(PROJECT_ROOT, threshold=200)
        assert isinstance(findings, list)
        for f in findings:
            assert "path" in f
            assert "lines" in f
            assert f["lines"] > 200


# ---------------------------------------------------------------------------
# 3. Static analyzer generates proposals from findings
# ---------------------------------------------------------------------------


class TestStaticProposalGeneration:
    """Static analyzer should produce Idea objects from findings."""

    def test_generate_proposals_returns_ideas(self):
        from project_forge.engine.static_introspect import generate_static_proposals

        proposals = generate_static_proposals(PROJECT_ROOT)
        assert isinstance(proposals, list)
        assert len(proposals) > 0  # Should always find something

        for idea in proposals:
            assert isinstance(idea, Idea)
            assert idea.category == IdeaCategory.SELF_IMPROVEMENT
            assert idea.feasibility_score >= 0.0
            assert idea.feasibility_score <= 1.0

    def test_proposals_reference_real_files(self):
        """Proposals should reference actual files in the codebase."""
        from project_forge.engine.static_introspect import generate_static_proposals

        proposals = generate_static_proposals(PROJECT_ROOT)
        for idea in proposals:
            # mvp_scope or description should mention src/ or tests/
            text = f"{idea.description} {idea.mvp_scope}"
            assert "src/" in text or "tests/" in text, f"Proposal '{idea.name}' doesn't reference files"

    def test_proposals_are_unique(self):
        """Proposals should not have duplicate names."""
        from project_forge.engine.static_introspect import generate_static_proposals

        proposals = generate_static_proposals(PROJECT_ROOT)
        names = [p.name for p in proposals]
        assert len(names) == len(set(names)), f"Duplicate proposal names: {names}"


# ---------------------------------------------------------------------------
# 4. introspect_runner works without API key
# ---------------------------------------------------------------------------


class TestIntrospectRunnerNoApiKey:
    """introspect_runner should work without ANTHROPIC_API_KEY using static fallback."""

    @pytest.mark.asyncio
    async def test_runner_generates_proposals_without_api_key(self):
        """Runner should generate static proposals when no API key is set."""
        from project_forge.cron.introspect_runner import run_introspect_cycle
        from project_forge.storage.db import Database

        mock_db = AsyncMock(spec=Database)
        mock_db.list_ideas = AsyncMock(return_value=[])
        mock_db.save_idea = AsyncMock()

        async def _mock_filter_and_save(idea, db_inst):
            await db_inst.save_idea(idea)
            return idea, True, None

        with patch(
            "project_forge.cron.introspect_runner.filter_and_save",
            side_effect=_mock_filter_and_save,
        ):
            # generator=None signals static mode
            result = await run_introspect_cycle(mock_db, generator=None)

        assert result is not None
        assert isinstance(result, Idea)
        assert result.category == IdeaCategory.SELF_IMPROVEMENT

    @pytest.mark.asyncio
    async def test_runner_static_proposals_stored_in_db(self):
        """Static proposals should be saved to the database."""
        from project_forge.cron.introspect_runner import run_introspect_cycle
        from project_forge.storage.db import Database

        mock_db = AsyncMock(spec=Database)
        mock_db.list_ideas = AsyncMock(return_value=[])
        mock_db.save_idea = AsyncMock()

        async def _mock_filter_and_save(idea, db_inst):
            await db_inst.save_idea(idea)
            return idea, True, None

        with patch(
            "project_forge.cron.introspect_runner.filter_and_save",
            side_effect=_mock_filter_and_save,
        ):
            await run_introspect_cycle(mock_db, generator=None)

        mock_db.save_idea.assert_called_once()


# ---------------------------------------------------------------------------
# 5. Forge Lab shows static proposals
# ---------------------------------------------------------------------------


class TestForgeLabStaticProposals:
    """Forge Lab page should show proposals from static introspection."""

    @pytest.mark.asyncio
    async def test_forge_lab_shows_proposals_after_static_run(self, client):
        """After static introspection, the Forge Lab should list proposals."""
        from project_forge.engine.static_introspect import generate_static_proposals

        # Generate and store a proposal
        proposals = generate_static_proposals(PROJECT_ROOT)
        if proposals:
            await db.save_idea(proposals[0])

        with patch("project_forge.scaffold.github.list_self_issues", return_value=[]):
            resp = await client.get("/thinktank")
        assert resp.status_code == 200
        # The proposal name should appear in the page
        if proposals:
            assert proposals[0].name in resp.text


# ---------------------------------------------------------------------------
# 6. generator.generate() accepts prompt_override
# ---------------------------------------------------------------------------


class TestGeneratorPromptOverride:
    """IdeaGenerator.generate() should accept prompt_override parameter."""

    @pytest.mark.asyncio
    async def test_generate_with_prompt_override(self):
        """generate() should use prompt_override instead of default prompt when provided."""
        from project_forge.engine.generator import IdeaGenerator

        gen = IdeaGenerator(api_key="test-key")

        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(
                text='{"name":"Test","tagline":"test","description":"test","category":"self-improvement",'
                '"market_analysis":"test","feasibility_score":0.8,"mvp_scope":"test","tech_stack":["python"]}'
            )
        ]

        with patch.object(gen.client.messages, "create", return_value=mock_response) as mock_create:
            idea = await gen.generate(
                category=IdeaCategory.SELF_IMPROVEMENT,
                prompt_override="Custom introspection prompt here",
            )

        # Should have used our custom prompt
        call_args = mock_create.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        assert messages[0]["content"] == "Custom introspection prompt here"
        assert idea.name == "Test"
