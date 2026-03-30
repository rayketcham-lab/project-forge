"""Tests for SQLite storage layer."""

import pytest

from project_forge.models import GenerationRun, Idea, IdeaCategory


@pytest.mark.asyncio
async def test_save_and_get_idea(db):
    idea = Idea(
        name="Test Idea",
        tagline="A test",
        description="Testing storage.",
        category=IdeaCategory.SECURITY_TOOL,
        market_analysis="Good market.",
        feasibility_score=0.75,
        mvp_scope="Build X.",
        tech_stack=["python"],
    )
    saved = await db.save_idea(idea)
    assert saved.id == idea.id

    fetched = await db.get_idea(idea.id)
    assert fetched is not None
    assert fetched.name == "Test Idea"
    assert fetched.feasibility_score == 0.75
    assert fetched.tech_stack == ["python"]


@pytest.mark.asyncio
async def test_get_nonexistent_idea(db):
    result = await db.get_idea("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_list_ideas_empty(db):
    ideas = await db.list_ideas()
    assert ideas == []


@pytest.mark.asyncio
async def test_list_ideas_with_filters(db):
    for i, cat in enumerate([IdeaCategory.SECURITY_TOOL, IdeaCategory.AUTOMATION, IdeaCategory.SECURITY_TOOL]):
        idea = Idea(
            name=f"Idea {i}",
            tagline=f"Tag {i}",
            description=f"Desc {i}",
            category=cat,
            market_analysis="Market",
            feasibility_score=0.5 + i * 0.1,
            mvp_scope="MVP",
        )
        await db.save_idea(idea)

    all_ideas = await db.list_ideas()
    assert len(all_ideas) == 3

    security_ideas = await db.list_ideas(category=IdeaCategory.SECURITY_TOOL)
    assert len(security_ideas) == 2

    auto_ideas = await db.list_ideas(category=IdeaCategory.AUTOMATION)
    assert len(auto_ideas) == 1


@pytest.mark.asyncio
async def test_update_idea_status(db):
    idea = Idea(
        name="Status Test",
        tagline="Tag",
        description="Desc",
        category=IdeaCategory.PRIVACY,
        market_analysis="Market",
        feasibility_score=0.6,
        mvp_scope="MVP",
    )
    await db.save_idea(idea)

    updated = await db.update_idea_status(idea.id, "approved")
    assert updated is not None
    assert updated.status == "approved"


@pytest.mark.asyncio
async def test_update_idea_urls(db):
    idea = Idea(
        name="URL Test",
        tagline="Tag",
        description="Desc",
        category=IdeaCategory.OBSERVABILITY,
        market_analysis="Market",
        feasibility_score=0.9,
        mvp_scope="MVP",
    )
    await db.save_idea(idea)

    updated = await db.update_idea_urls(idea.id, github_issue_url="https://github.com/test/1")
    assert updated is not None
    assert updated.github_issue_url == "https://github.com/test/1"


@pytest.mark.asyncio
async def test_count_ideas(db):
    for i in range(3):
        idea = Idea(
            name=f"Count {i}",
            tagline="Tag",
            description="Desc",
            category=IdeaCategory.COMPLIANCE,
            market_analysis="Market",
            feasibility_score=0.5,
            mvp_scope="MVP",
        )
        await db.save_idea(idea)

    assert await db.count_ideas() == 3
    assert await db.count_ideas(status="new") == 3
    assert await db.count_ideas(status="approved") == 0


@pytest.mark.asyncio
async def test_get_recent_categories(db):
    for cat in [IdeaCategory.SECURITY_TOOL, IdeaCategory.AUTOMATION, IdeaCategory.PRIVACY]:
        idea = Idea(
            name=f"Cat {cat.value}",
            tagline="Tag",
            description="Desc",
            category=cat,
            market_analysis="Market",
            feasibility_score=0.5,
            mvp_scope="MVP",
        )
        await db.save_idea(idea)

    recent = await db.get_recent_categories(limit=2)
    assert len(recent) == 2


@pytest.mark.asyncio
async def test_save_and_get_stats(db):
    idea = Idea(
        name="Stats Test",
        tagline="Tag",
        description="Desc",
        category=IdeaCategory.DEVOPS_TOOLING,
        market_analysis="Market",
        feasibility_score=0.8,
        mvp_scope="MVP",
    )
    await db.save_idea(idea)

    run = GenerationRun(category=IdeaCategory.DEVOPS_TOOLING, idea_id=idea.id, success=True)
    await db.save_run(run)

    stats = await db.get_stats()
    assert stats["total_ideas"] >= 1
    assert stats["total_runs"] >= 1
    assert stats["avg_feasibility_score"] > 0


@pytest.mark.asyncio
async def test_save_generation_run(db):
    run = GenerationRun(category=IdeaCategory.MARKET_GAP, success=False, error="API timeout")
    saved = await db.save_run(run)
    assert saved.id == run.id
