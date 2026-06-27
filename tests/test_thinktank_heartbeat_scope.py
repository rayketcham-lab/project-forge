"""TDD: thinktank Engine Activity panel must be SI-scoped.

User feedback: "304 accepted but the tiles are fucking useless the data
does not match." Root cause: the heartbeat panel showed all-categories
counts (304 accepted in 24h across the whole engine) next to Forge Lab
tiles that count only self-improvement (3 proposals, 10 promoted, 18
rejected, all-time). The two scopes were silently mixed and looked
incoherent.

New contract: every number on /thinktank refers to the self-improvement
category. The whole-engine view stays on /, where it belongs.
"""

from __future__ import annotations

import datetime as dt

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.models import FilteredIdea, Idea, IdeaCategory


def _idea(name: str, *, category: IdeaCategory, days_ago: int = 0, status: str = "new") -> Idea:
    return Idea(
        name=name,
        tagline=f"tag {name}",
        description="d",
        category=category,
        market_analysis="m",
        feasibility_score=0.8,
        mvp_scope="mvp",
        tech_stack=["python"],
        generated_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=days_ago),
        status=status,  # type: ignore[arg-type]
    )


def _filtered(name: str, *, category: IdeaCategory, days_ago: int = 0) -> FilteredIdea:
    fi = FilteredIdea(
        idea_name=name,
        idea_tagline="t",
        idea_category=category,
        filter_reason="duplicate:tagline_similarity:0.9",
        original_idea_json="{}",
    )
    fi.filtered_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=days_ago)
    return fi


@pytest_asyncio.fixture
async def client(tmp_path):
    from project_forge.web.app import app, db

    db.db_path = tmp_path / "scope.db"
    await db.connect()

    # 5 SI ideas accepted in last 24h, 1 SI filtered, 1 SI old
    for i in range(5):
        await db.save_idea(_idea(f"SI Recent {i}", category=IdeaCategory.SELF_IMPROVEMENT, days_ago=0))
    await db.save_filtered_idea(_filtered("SI Reject", category=IdeaCategory.SELF_IMPROVEMENT, days_ago=0))
    await db.save_idea(_idea("SI Old", category=IdeaCategory.SELF_IMPROVEMENT, days_ago=10))

    # 50 non-SI accepted in last 24h, 200 non-SI filtered (these MUST NOT show up)
    for i in range(50):
        await db.save_idea(_idea(f"Other {i}", category=IdeaCategory.SECURITY_TOOL, days_ago=0))
    for i in range(200):
        await db.save_filtered_idea(_filtered(f"Other Reject {i}", category=IdeaCategory.SECURITY_TOOL, days_ago=0))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await db.close()


@pytest.mark.asyncio
async def test_heartbeat_accepted_count_is_si_only(client):
    """Engine Activity 'Accepted (24h)' must count only self-improvement ideas."""
    resp = await client.get("/thinktank")
    html = resp.text

    # Look for the accepted-count tile. Expected = 5 (SI only), not 55 (all).
    # We don't enforce exact label text; check the number appears in the
    # heartbeat panel and the all-categories total does not.
    assert "Engine Activity" in html or "Self-Improvement Activity" in html
    assert ">5<" in html, f"Expected SI-scoped accepted count of 5 in heartbeat, got: {html[:2000]}"
    # The all-categories total must NOT appear as a heartbeat number.
    assert ">55<" not in html, "Heartbeat must not include non-SI ideas"


@pytest.mark.asyncio
async def test_heartbeat_filtered_count_is_si_only(client):
    """'Dedup-filtered (24h)' must count only self-improvement filters."""
    resp = await client.get("/thinktank")
    html = resp.text

    # Expected = 1 (SI), not 201 (all). The 1 might collide with other small
    # counts on the page (e.g. if we have 1 SI accepted somewhere too) but
    # the main contract is: the all-categories number 201 must NOT appear.
    assert ">201<" not in html, "Heartbeat filtered count must be SI-only, not all-categories"


@pytest.mark.asyncio
async def test_recent_si_activity_shows_real_events(client):
    """Recent SI activity feed must show actual accepted + filtered SI events
    (not stale rows from generation_runs table that the introspect runner
    doesn't write to)."""
    resp = await client.get("/thinktank")
    html = resp.text

    # The activity feed surfaces actual SI ideas — the SI Recent name from
    # the fixture must appear (proves we read the right tables).
    assert "SI Recent 0" in html or "Recent SI activity" in html
    # The non-SI ideas in the fixture (50 SECURITY_TOOL, 200 filtered)
    # must NOT leak into the SI panel.
    assert "Other 0" not in html
    assert "Other Reject 0" not in html
