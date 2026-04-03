"""Tests for cron scheduler and runner."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio

from project_forge.cron.scheduler import (
    create_github_issue_for_idea,
    generate_and_store,
    pick_category,
    scaffold_project,
)
from project_forge.engine.generator import IdeaGenerator
from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test_cron.db")
    await database.connect()
    yield database
    await database.close()


def _make_idea(**kwargs) -> Idea:
    defaults = {
        "name": "Test Cron Idea",
        "tagline": "A cron test",
        "description": "Testing cron.",
        "category": IdeaCategory.AUTOMATION,
        "market_analysis": "Market.",
        "feasibility_score": 0.8,
        "mvp_scope": "MVP.",
        "tech_stack": ["python"],
    }
    defaults.update(kwargs)
    return Idea(**defaults)


MOCK_API_RESPONSE = json.dumps(
    {
        "name": "Cron Generated Idea",
        "tagline": "Auto-generated automation idea for CI pipeline monitoring",
        "description": (
            "CI pipeline monitoring is critical for catching flaky tests and slow builds. "
            "This tool watches GitHub Actions runs, flags regressions, and generates reports "
            "on test stability trends over time using the GitHub API."
        ),
        "category": "automation",
        "market_analysis": "Teams need better visibility into CI health and test flakiness.",
        "feasibility_score": 0.75,
        "mvp_scope": "CLI tool that queries GitHub Actions API, outputs JSON reports on test stability.",
        "tech_stack": ["python", "click"],
    }
)


@pytest.mark.asyncio
async def test_pick_category_avoids_recent(db):
    # Save ideas in specific categories
    for cat in [IdeaCategory.SECURITY_TOOL, IdeaCategory.AUTOMATION, IdeaCategory.PRIVACY]:
        idea = _make_idea(category=cat)
        await db.save_idea(idea)

    # Pick should avoid recent 3
    category = await pick_category(db)
    assert category in IdeaCategory


@pytest.mark.asyncio
async def test_pick_category_empty_db(db):
    category = await pick_category(db)
    assert category in IdeaCategory


@pytest.mark.asyncio
@patch("project_forge.engine.generator.anthropic.Anthropic")
async def test_generate_and_store(mock_anthropic_cls, db):
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_content = MagicMock()
    mock_content.text = MOCK_API_RESPONSE
    mock_response = MagicMock()
    mock_response.content = [mock_content]
    mock_client.messages.create.return_value = mock_response

    generator = IdeaGenerator(api_key="test-key")
    idea = await generate_and_store(db, generator)

    assert idea.name == "Cron Generated Idea"

    # Verify it was stored
    stored = await db.get_idea(idea.id)
    assert stored is not None
    assert stored.name == "Cron Generated Idea"

    # Verify run was recorded
    stats = await db.get_stats()
    assert stats["total_runs"] >= 1


@pytest.mark.asyncio
@patch("project_forge.engine.generator.anthropic.Anthropic")
async def test_generate_and_store_records_failure(mock_anthropic_cls, db):
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.side_effect = RuntimeError("API error")

    generator = IdeaGenerator(api_key="test-key")

    with pytest.raises(RuntimeError, match="API error"):
        await generate_and_store(db, generator)

    # Verify failure run was recorded
    stats = await db.get_stats()
    assert stats["total_runs"] >= 1


@patch("project_forge.cron.scheduler.create_issue")
@patch("project_forge.cron.scheduler.create_label")
@pytest.mark.asyncio
async def test_create_github_issue_for_idea(mock_label, mock_issue):
    mock_issue.return_value = "https://github.com/rayketcham-lab/project-forge/issues/1"
    idea = _make_idea()

    url = await create_github_issue_for_idea(idea)
    assert "issues/1" in url
    mock_issue.assert_called_once()


@patch("project_forge.cron.scheduler.push_initial_commit")
@patch("project_forge.cron.scheduler.create_repo")
@patch("project_forge.cron.scheduler.create_issue")
def test_scaffold_project(mock_issue, mock_create_repo, mock_push):
    mock_create_repo.return_value = "https://github.com/rayketcham/test-cron-idea"
    mock_issue.return_value = "https://github.com/rayketcham/test-cron-idea/issues/1"

    idea = _make_idea()
    url = scaffold_project(idea)

    assert url is not None
    assert "test-cron-idea" in url
    mock_create_repo.assert_called_once()
    mock_push.assert_called_once()
