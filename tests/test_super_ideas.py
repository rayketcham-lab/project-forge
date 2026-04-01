"""Tests for Super Ideas - daily mega-project synthesis from all ideas."""

from pathlib import Path

import pytest
import pytest_asyncio

from project_forge.cron.auto_scan import run_auto_scan
from project_forge.engine.super_ideas import (
    SuperIdea,
    SuperIdeaGenerator,
    find_idea_clusters,
    synthesize_super_idea,
)
from project_forge.models import IdeaCategory
from project_forge.storage.db import Database


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test_super.db")
    await database.connect()
    # Seed with 50 ideas to have enough for clustering
    await run_auto_scan(database, count=50)
    yield database
    await database.close()


class TestSuperIdeaModel:
    def test_super_idea_creation(self):
        si = SuperIdea(
            name="Quantum PKI Platform",
            tagline="End-to-end post-quantum PKI management",
            description="A comprehensive platform combining CRL optimization and OCSP benchmarking.",
            vision="Become the standard platform for PQC PKI transition.",
            component_idea_ids=["abc123", "def456", "ghi789"],
            categories_spanned=[IdeaCategory.PQC_CRYPTOGRAPHY, IdeaCategory.CRYPTO_INFRASTRUCTURE],
            combined_feasibility=0.82,
            impact_score=0.90,
            tech_stack=["python", "rust", "openssl"],
            mvp_phases=["Phase 1: CRL analyzer", "Phase 2: OCSP bench", "Phase 3: Lifecycle mgmt"],
        )
        assert si.name == "Quantum PKI Platform"
        assert si.is_super is True
        assert len(si.component_idea_ids) == 3
        assert len(si.mvp_phases) == 3
        assert si.impact_score == 0.90

    def test_super_idea_serialization(self):
        si = SuperIdea(
            name="Test Super",
            tagline="Test",
            description="Test",
            vision="Test vision",
            component_idea_ids=["a", "b"],
            categories_spanned=[IdeaCategory.SECURITY_TOOL],
            combined_feasibility=0.75,
            impact_score=0.85,
            tech_stack=["python"],
            mvp_phases=["Phase 1"],
        )
        data = si.model_dump()
        restored = SuperIdea(**data)
        assert restored.name == si.name
        assert restored.is_super is True


class TestIdeaClustering:
    @pytest.mark.asyncio
    async def test_find_clusters(self, db):
        ideas = await db.list_ideas(limit=50)
        clusters = find_idea_clusters(ideas)
        assert len(clusters) >= 3, "Should find at least 3 clusters from 50 ideas"
        for cluster in clusters:
            assert "theme" in cluster
            assert "ideas" in cluster
            assert len(cluster["ideas"]) >= 2

    @pytest.mark.asyncio
    async def test_clusters_have_themes(self, db):
        ideas = await db.list_ideas(limit=50)
        clusters = find_idea_clusters(ideas)
        themes = [c["theme"] for c in clusters]
        assert all(len(t) > 5 for t in themes), "Themes should be descriptive"

    @pytest.mark.asyncio
    async def test_clusters_span_categories(self, db):
        ideas = await db.list_ideas(limit=50)
        clusters = find_idea_clusters(ideas)
        # At least one cluster should span multiple categories
        multi_cat = [c for c in clusters if len(set(i.category for i in c["ideas"])) > 1]
        assert len(multi_cat) >= 1


class TestSuperIdeaSynthesis:
    @pytest.mark.asyncio
    async def test_synthesize_from_cluster(self, db):
        ideas = await db.list_ideas(limit=50)
        clusters = find_idea_clusters(ideas)
        cluster = clusters[0]
        super_idea = synthesize_super_idea(cluster)
        assert super_idea.name
        assert super_idea.is_super is True
        assert len(super_idea.component_idea_ids) >= 2
        assert len(super_idea.mvp_phases) >= 2
        assert super_idea.impact_score > 0
        assert super_idea.vision


class TestSuperIdeaGenerator:
    @pytest.mark.asyncio
    async def test_generate_5_super_ideas(self, db):
        gen = SuperIdeaGenerator(db)
        supers = await gen.generate(count=5)
        assert len(supers) == 5
        for si in supers:
            assert si.is_super is True
            assert si.impact_score > 0
            assert len(si.component_idea_ids) >= 2
            assert len(si.mvp_phases) >= 2

    @pytest.mark.asyncio
    async def test_super_ideas_stored_in_db(self, db):
        gen = SuperIdeaGenerator(db)
        supers = await gen.generate(count=5)
        # Super ideas get stored as regular ideas with "super" in their metadata
        for si in supers:
            stored = await db.get_idea(si.id)
            assert stored is not None

    @pytest.mark.asyncio
    async def test_super_ideas_are_unique(self, db):
        gen = SuperIdeaGenerator(db)
        supers = await gen.generate(count=5)
        names = [s.name for s in supers]
        assert len(set(names)) == 5


class TestSuperIdeaGeneratorEmptyPool:
    """Verify the early-exit guard when the idea pool is too small."""

    @pytest_asyncio.fixture
    async def sparse_db(self, tmp_path: Path):
        """A fresh DB with fewer than 10 ideas."""
        from project_forge.models import Idea, IdeaCategory

        database = Database(tmp_path / "sparse.db")
        await database.connect()
        for i in range(5):
            idea = Idea(
                name=f"Sparse Idea {i}",
                tagline="Tag",
                description="Desc",
                category=IdeaCategory.SECURITY_TOOL,
                market_analysis="Market",
                feasibility_score=0.5,
                mvp_scope="MVP",
            )
            await database.save_idea(idea)
        yield database
        await database.close()

    @pytest.mark.asyncio
    async def test_generate_returns_empty_when_too_few_ideas(self, sparse_db):
        """generate() must return [] when there are fewer than 10 ideas."""
        gen = SuperIdeaGenerator(sparse_db)
        result = await gen.generate(count=5)
        assert result == [], (
            f"Expected empty list with only 5 ideas in pool, got {result}"
        )
