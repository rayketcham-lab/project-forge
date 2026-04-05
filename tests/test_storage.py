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
    taglines = [
        "audit SOC2 control mapping gaps",
        "validate GDPR data residency requirements",
        "track FedRAMP authorization boundaries",
    ]
    for i in range(3):
        idea = Idea(
            name=f"Count {i}",
            tagline=taglines[i],
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


@pytest.mark.asyncio
async def test_list_ideas_pagination(db):
    """Pagination: limit=5 offset=0 and limit=5 offset=5 must return disjoint sets."""
    for i in range(10):
        idea = Idea(
            name=f"Paginated Idea {i:02d}",
            tagline=f"Tag {i}",
            description=f"Desc {i}",
            category=IdeaCategory.AUTOMATION,
            market_analysis="Market",
            feasibility_score=0.5,
            mvp_scope="MVP",
        )
        await db.save_idea(idea)

    page1 = await db.list_ideas(limit=5, offset=0)
    page2 = await db.list_ideas(limit=5, offset=5)

    assert len(page1) == 5
    assert len(page2) == 5

    ids_page1 = {i.id for i in page1}
    ids_page2 = {i.id for i in page2}
    assert ids_page1.isdisjoint(ids_page2), "Paginated pages must not share any ideas"


@pytest.mark.asyncio
async def test_list_ideas_offset_beyond_end(db):
    """Querying past the last row must return an empty list."""
    idea = Idea(
        name="Only Idea",
        tagline="Tag",
        description="Desc",
        category=IdeaCategory.AUTOMATION,
        market_analysis="Market",
        feasibility_score=0.5,
        mvp_scope="MVP",
    )
    await db.save_idea(idea)

    result = await db.list_ideas(limit=5, offset=100)
    assert result == [], f"Expected empty list, got {result}"


@pytest.mark.asyncio
async def test_list_super_ideas_deduplicates_by_name(db):
    """list_super_ideas must return exactly one row per unique name, with the highest score."""
    super_taglines = [
        "lattice-based certificate authority management",
        "hybrid classical quantum key distribution bridge",
        "post-quantum TLS handshake optimization engine",
    ]
    for i, score in enumerate((0.7, 0.8, 0.9)):
        idea = Idea(
            name="[SUPER] Quantum PKI Platform",
            tagline=super_taglines[i],
            description="Desc",
            category=IdeaCategory.PQC_CRYPTOGRAPHY,
            market_analysis="Market",
            feasibility_score=score,
            mvp_scope="Build it.",
        )
        await db.save_idea(idea)

    results = await db.list_super_ideas(limit=10)
    assert len(results) == 1, f"Expected exactly 1 deduplicated super idea, got {len(results)}"
    assert results[0].feasibility_score == 0.9, (
        f"Expected highest-scoring row (0.9), got {results[0].feasibility_score}"
    )
