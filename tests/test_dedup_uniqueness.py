"""INSERT-time uniqueness gates beyond the original tagline-Jaccard check.

The one-shot siphon trims existing dupes; these tests pin down the
gates that stop dupes from regrowing the moment generation resumes.

Three new checks layered into `should_accept`:
- name Jaccard ≥ 0.55 across the same category (mirrors siphon name threshold)
- vertical-cap-2: at most 2 active ideas per stripped 'X for {vertical}' concept
- super-idea component-overlap: ≥3 atoms shared with any existing super → reject
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from project_forge.engine.dedup import should_accept
from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database


def _idea(
    name: str,
    tagline: str = "default tagline body for tests",
    *,
    description: str = "default description body for tests",
    category: IdeaCategory = IdeaCategory.OBSERVABILITY,
    score: float = 0.8,
) -> Idea:
    return Idea(
        name=name,
        tagline=tagline,
        description=description,
        category=category,
        market_analysis="m",
        feasibility_score=score,
        mvp_scope="mvp",
        tech_stack=["python"],
    )


def _super_idea(name: str, components: list[str], *, score: float = 0.85) -> Idea:
    body = (
        f"{name} brings together {len(components)} complementary project "
        "concepts into a single, cohesive platform:\n\n"
    )
    body += "\n".join(f"- **{c}**: blurb for {c}" for c in components)
    return _idea(
        name=f"[SUPER] {name}",
        tagline=f"tag for {name}",
        description=body,
        score=score,
    )


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "uniq.db")
    await database.connect()
    yield database
    await database.close()


class TestRegularNameSimilarity:
    @pytest.mark.asyncio
    async def test_rejects_high_name_jaccard_same_category(self, db):
        await db.save_idea(
            _idea("Distributed Tracing Anomaly Detector", "old tagline alpha"),
        )

        # Different tagline so tagline-similarity wouldn't fire, but name
        # tokens overlap strongly.
        cand = _idea(
            "Tracing Anomaly Detector Distributed",
            "entirely different words about something else",
        )
        accepted, reason = await should_accept(cand, db)
        assert accepted is False
        assert "name_similarity" in (reason or "")

    @pytest.mark.asyncio
    async def test_accepts_low_name_jaccard(self, db):
        await db.save_idea(_idea("Service Mesh Latency Visualizer", "a"))
        cand = _idea("Configuration Audit Tracker", "b")
        accepted, _ = await should_accept(cand, db)
        assert accepted is True

    @pytest.mark.asyncio
    async def test_name_check_scoped_to_same_category(self, db):
        await db.save_idea(_idea(
            "Distributed Tracing Anomaly Detector",
            "x",
            category=IdeaCategory.OBSERVABILITY,
        ))
        # Same name tokens, different category — must NOT reject.
        cand = _idea(
            "Tracing Anomaly Detector Distributed",
            "y",
            category=IdeaCategory.PRIVACY,
        )
        accepted, _ = await should_accept(cand, db)
        assert accepted is True


class TestVerticalCapAtInsert:
    @pytest.mark.asyncio
    async def test_third_clone_rejected(self, db):
        await db.save_idea(_idea(
            "Pqc Tracker for Healthcare", "one tagline",
            category=IdeaCategory.PQC_CRYPTOGRAPHY,
        ))
        await db.save_idea(_idea(
            "Pqc Tracker for Financial", "two tagline aaaa",
            category=IdeaCategory.PQC_CRYPTOGRAPHY,
        ))

        cand = _idea(
            "Pqc Tracker for Container", "three tagline aaaa bbbb",
            category=IdeaCategory.PQC_CRYPTOGRAPHY,
        )
        accepted, reason = await should_accept(cand, db)
        assert accepted is False
        assert "vertical_cap" in (reason or "")

    @pytest.mark.asyncio
    async def test_first_two_clones_accepted(self, db):
        a = _idea("Pqc Tracker for Healthcare", "alpha")
        accepted_a, _ = await should_accept(a, db)
        assert accepted_a is True
        await db.save_idea(a)

        b = _idea("Pqc Tracker for Financial", "beta beta beta")
        accepted_b, _ = await should_accept(b, db)
        assert accepted_b is True

    @pytest.mark.asyncio
    async def test_ignores_non_pattern_names(self, db):
        await db.save_idea(_idea("Standalone Engine", "x"))
        await db.save_idea(_idea("Another Standalone Concept", "y"))
        cand = _idea("Yet Another Engine", "z")
        accepted, _ = await should_accept(cand, db)
        assert accepted is True


class TestSuperComponentOverlapAtInsert:
    @pytest.mark.asyncio
    async def test_rejects_super_with_overlapping_components(self, db):
        existing = _super_idea(
            "First Theme",
            ["Atom A", "Atom B", "Atom C", "Atom D", "Atom E", "Atom F"],
            score=0.9,
        )
        await db.save_idea(existing)

        # Candidate shares 4 of 6 atoms — should be rejected.
        cand = _super_idea(
            "Different-Sounding Theme",
            ["Atom A", "Atom B", "Atom C", "Atom D", "Atom X", "Atom Y"],
            score=0.85,
        )
        accepted, reason = await should_accept(cand, db)
        assert accepted is False
        assert "super_overlap" in (reason or "")

    @pytest.mark.asyncio
    async def test_accepts_super_with_disjoint_components(self, db):
        existing = _super_idea(
            "First Theme",
            ["A1", "A2", "A3", "A4", "A5", "A6"],
        )
        await db.save_idea(existing)

        cand = _super_idea(
            "Second Theme",
            ["B1", "B2", "B3", "B4", "B5", "B6"],
        )
        accepted, _ = await should_accept(cand, db)
        assert accepted is True
