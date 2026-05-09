"""TDD: dashboard stats must reflect ACTIVE corpus, not total rows.

After the siphon archived 1,739 rows, the "Total Ideas" tile still showed
4,616 (includes archived) which is misleading — the active corpus is
~400. Fix get_stats() to separate active from archived, and use active
as the headline metric.

Also: avg_feasibility_score should compute over ACTIVE ideas only,
otherwise the average is dragged toward the archived rows' historical
distribution rather than the corpus the user sees.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.models import Idea, IdeaCategory


def _idea(name: str = "X", score: float = 0.7,
          status: str = "new",
          category: IdeaCategory = IdeaCategory.SECURITY_TOOL) -> Idea:
    i = Idea(
        name=name,
        tagline=f"tag for {name}",
        description="d",
        category=category,
        market_analysis="m",
        feasibility_score=score,
        mvp_scope="mvp",
        tech_stack=["python"],
    )
    return i


@pytest_asyncio.fixture
async def db(tmp_path):
    from project_forge.storage.db import Database
    d = Database(tmp_path / "stats.db")
    await d.connect()
    yield d
    await d.close()


@pytest_asyncio.fixture
async def client(tmp_path):
    from project_forge.web.app import app, db

    db.db_path = tmp_path / "stats_client.db"
    await db.connect()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, db
    await db.close()


# ── get_stats: active vs archived split ──────────────────────────────


class TestStatsActiveVsArchived:
    @pytest.mark.asyncio
    async def test_total_active_excludes_archived(self, db):
        # 3 active, 5 archived, 2 rejected
        for i in range(3):
            await db.save_idea(_idea(f"A{i}", status="new"))
        for i in range(5):
            arch = _idea(f"R{i}")
            await db.save_idea(arch)
            await db.update_idea_status(arch.id, "archived")
        for i in range(2):
            rej = _idea(f"X{i}")
            await db.save_idea(rej)
            await db.update_idea_status(rej.id, "rejected")

        stats = await db.get_stats()
        assert stats.get("total_active") == 3, (
            f"total_active should exclude archived+rejected; got {stats.get('total_active')}"
        )
        assert stats.get("total_archived") == 5, (
            f"total_archived should be exactly the 'archived' status count; "
            f"got {stats.get('total_archived')}"
        )

    @pytest.mark.asyncio
    async def test_avg_feasibility_active_only(self, db):
        # 2 active scoring 0.9, 5 archived scoring 0.3 (would drag the avg way down)
        for _ in range(2):
            await db.save_idea(_idea(score=0.9, status="new"))
        for _ in range(5):
            arch = _idea(score=0.3)
            await db.save_idea(arch)
            await db.update_idea_status(arch.id, "archived")

        stats = await db.get_stats()
        avg_active = stats.get("avg_feasibility_active")
        assert avg_active is not None, "stats must expose avg_feasibility_active"
        assert avg_active == pytest.approx(0.9, abs=0.05), (
            f"Average should be 0.9 (the active two); got {avg_active}. "
            f"If you see ~0.47, the avg includes archived — the bug."
        )


# ── Dashboard rendering uses the active count ────────────────────────


@pytest.mark.asyncio
async def test_dashboard_total_tile_shows_active_not_total(client):
    """The 'Total Ideas' / equivalent headline tile must NOT show the
    sum-with-archived. After the siphon many users will see 80%+ archived.
    """
    c, db = client
    # 4 active, 96 archived
    for i in range(4):
        await db.save_idea(_idea(f"A{i}"))
    for i in range(96):
        arch = _idea(f"old{i}")
        await db.save_idea(arch)
        await db.update_idea_status(arch.id, "archived")

    resp = await c.get("/")
    html = resp.text

    # The big "Total Ideas" tile must read 4 (active), not 100 (sum)
    # We can't pin position, but assert: nowhere in the visible stats grid
    # does ">100<" appear as a stat-number.
    import re
    big_numbers = re.findall(
        r'class="stat-number">\s*(\d+)\s*<', html,
    )
    assert "100" not in big_numbers, (
        f"A stat tile renders 100 (the sum). Stats now: {big_numbers}. "
        f"Should show 4 for active, archived in a different tile/badge."
    )
    assert "4" in big_numbers, (
        f"Active-count 4 is missing from the stat tiles: {big_numbers}"
    )
