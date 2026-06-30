"""Self-improvement ideas must never be fed into super-idea synthesis.

Bundling concrete code tasks into "[SUPER] X + Y synthesized into one platform"
ideas is meaningless and flooded the Think Tank with floaty noise. The synthesis
pool must exclude SELF_IMPROVEMENT entirely.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from project_forge.engine.super_ideas import SuperIdeaGenerator
from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "si_super.db")
    await database.connect()
    yield database
    await database.close()


def _si(i: int) -> Idea:
    # Similar names/taglines so they WOULD cluster if not excluded.
    return Idea(
        name=f"Add tests for module_{i}",
        tagline="missing test coverage for an internal module",
        description="The module has no test coverage; add pytest cases for its public functions." * 2,
        category=IdeaCategory.SELF_IMPROVEMENT,
        market_analysis="Internal quality improvement — reduces bug risk.",
        feasibility_score=0.8,
        mvp_scope=f"Create tests/test_module_{i}.py with smoke tests.",
        tech_stack=["python", "pytest"],
        content_hash=f"si{i}",
    )


def _real(i: int) -> Idea:
    return Idea(
        name=f"Security Scanner {i}",
        tagline=f"continuous secrets scanning for repos variant {i}",
        description="A tool that scans repositories for leaked secrets and misconfigurations across CI." * 2,
        category=IdeaCategory.SECURITY_TOOL,
        market_analysis="Security teams need continuous secret detection.",
        feasibility_score=0.7,
        mvp_scope="Phase 1: scan. Phase 2: report.",
        tech_stack=["python"],
        content_hash=f"real{i}",
    )


class TestSIExcludedFromSynthesis:
    @pytest.mark.asyncio
    async def test_si_only_db_produces_no_supers(self, db):
        for i in range(15):
            await db.save_idea(_si(i))
        gen = SuperIdeaGenerator(db)
        supers = await gen.generate(count=5)
        assert supers == []  # pool empty after excluding SI
        cur = await db.db.execute(
            "SELECT COUNT(*) FROM ideas WHERE name LIKE '[SUPER]%' AND category = 'self-improvement'"
        )
        assert (await cur.fetchone())[0] == 0

    @pytest.mark.asyncio
    async def test_mixed_db_never_makes_a_self_improvement_super(self, db):
        for i in range(15):
            await db.save_idea(_si(i))
        for i in range(14):
            await db.save_idea(_real(i))
        gen = SuperIdeaGenerator(db)
        await gen.generate(count=5)
        # Any supers that got created must NOT be self-improvement.
        cur = await db.db.execute(
            "SELECT COUNT(*) FROM ideas WHERE name LIKE '[SUPER]%' AND category = 'self-improvement'"
        )
        assert (await cur.fetchone())[0] == 0
