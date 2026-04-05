"""Tests for universal fuzzy dedup and filtered-idea audit trail.

TDD RED phase: These tests define the desired behavior for:
1. Universal fuzzy dedup — all categories, not just self-improvement
2. FilteredIdea model — Pydantic model for rejected/filtered ideas
3. filtered_ideas table — audit trail for ideas blocked by dedup or quality review
4. Dedup stats — counts by filter_reason and category
"""

from datetime import datetime
from pathlib import Path

import pytest
import pytest_asyncio

from project_forge.models import FilteredIdea, Idea, IdeaCategory
from project_forge.storage.db import Database


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test_universal_dedup.db")
    await database.connect()
    yield database
    await database.close()


def _idea(name: str, tagline: str, category: IdeaCategory = IdeaCategory.SECURITY_TOOL, **kw) -> Idea:
    """Helper to create an idea with minimal boilerplate."""
    return Idea(
        name=name,
        tagline=tagline,
        description=kw.pop("description", "A detailed description of the idea for testing purposes."),
        category=category,
        market_analysis=kw.pop("market_analysis", "Strong market need."),
        feasibility_score=kw.pop("feasibility_score", 0.8),
        mvp_scope=kw.pop("mvp_scope", "Build CLI tool with core features."),
        tech_stack=kw.pop("tech_stack", ["python"]),
        **kw,
    )


# ---------------------------------------------------------------------------
# 1. FilteredIdea model
# ---------------------------------------------------------------------------


class TestFilteredIdeaModel:
    """FilteredIdea is a Pydantic model for the audit trail."""

    def test_create_filtered_idea_with_required_fields(self):
        fi = FilteredIdea(
            idea_name="Cert Scanner Pro",
            idea_tagline="scan certificates for expiry",
            idea_category=IdeaCategory.SECURITY_TOOL,
            filter_reason="duplicate:tagline_similarity:0.85",
            original_idea_json='{"name": "Cert Scanner Pro"}',
        )
        assert fi.idea_name == "Cert Scanner Pro"
        assert fi.filter_reason == "duplicate:tagline_similarity:0.85"
        assert fi.similar_to_id is None  # Optional, defaults to None

    def test_filtered_idea_has_auto_id(self):
        fi = FilteredIdea(
            idea_name="Test",
            idea_tagline="test",
            idea_category=IdeaCategory.AUTOMATION,
            filter_reason="duplicate:content_hash",
            original_idea_json="{}",
        )
        assert fi.id is not None
        assert len(fi.id) == 12

    def test_filtered_idea_has_timestamp(self):
        fi = FilteredIdea(
            idea_name="Test",
            idea_tagline="test",
            idea_category=IdeaCategory.AUTOMATION,
            filter_reason="quality:buzzwords",
            original_idea_json="{}",
        )
        assert isinstance(fi.filtered_at, datetime)

    def test_filtered_idea_with_similar_to_id(self):
        fi = FilteredIdea(
            idea_name="Test",
            idea_tagline="test",
            idea_category=IdeaCategory.AUTOMATION,
            filter_reason="duplicate:tagline_similarity:0.92",
            original_idea_json="{}",
            similar_to_id="abc123",
        )
        assert fi.similar_to_id == "abc123"


# ---------------------------------------------------------------------------
# 2. Universal fuzzy dedup — all categories
# ---------------------------------------------------------------------------


class TestUniversalFuzzyDedup:
    """save_idea should reject near-duplicate taglines in ANY category."""

    @pytest.mark.asyncio
    async def test_security_tool_near_dup_rejected(self, db):
        """SECURITY_TOOL ideas with near-duplicate taglines should be deduped."""
        original = _idea(
            "PKI Scanner V1",
            "scan PKI infrastructure for certificate issues — enterprise edition",
            category=IdeaCategory.SECURITY_TOOL,
        )
        await db.save_idea(original)

        near_dup = _idea(
            "PKI Scanner V2",
            "scan PKI infrastructure for certificate issues — community edition",
            category=IdeaCategory.SECURITY_TOOL,
        )
        await db.save_idea(near_dup)

        all_ideas = await db.list_ideas(category=IdeaCategory.SECURITY_TOOL, limit=100)
        assert len(all_ideas) == 1, "Near-duplicate security-tool idea should be blocked"
        assert all_ideas[0].name == "PKI Scanner V1"

    @pytest.mark.asyncio
    async def test_pqc_near_dup_rejected(self, db):
        """PQC_CRYPTOGRAPHY ideas with near-duplicate taglines should be deduped."""
        original = _idea(
            "Lattice Key Exchange Suite",
            "post-quantum lattice-based key exchange implementation — optimized for TLS",
            category=IdeaCategory.PQC_CRYPTOGRAPHY,
        )
        await db.save_idea(original)

        near_dup = _idea(
            "Lattice Key Exchange Hub",
            "post-quantum lattice-based key exchange implementation — optimized for SSH",
            category=IdeaCategory.PQC_CRYPTOGRAPHY,
        )
        await db.save_idea(near_dup)

        count = await db.count_ideas()
        assert count == 1, "Near-duplicate PQC idea should be blocked"

    @pytest.mark.asyncio
    async def test_different_ideas_same_category_both_saved(self, db):
        """Genuinely different ideas in the same category should both persist."""
        idea1 = _idea(
            "Cert Expiry Monitor",
            "monitor TLS certificates for upcoming expiration dates",
            category=IdeaCategory.SECURITY_TOOL,
        )
        idea2 = _idea(
            "Vuln Dependency Scanner",
            "scan project dependencies for known CVE vulnerabilities",
            category=IdeaCategory.SECURITY_TOOL,
        )
        await db.save_idea(idea1)
        await db.save_idea(idea2)

        all_ideas = await db.list_ideas(category=IdeaCategory.SECURITY_TOOL, limit=100)
        assert len(all_ideas) == 2

    @pytest.mark.asyncio
    async def test_cross_category_similar_taglines_both_saved(self, db):
        """Similar taglines in DIFFERENT categories should both persist (scoped dedup)."""
        sec_idea = _idea(
            "Cert Monitor Security",
            "monitor certificates for security compliance issues",
            category=IdeaCategory.SECURITY_TOOL,
        )
        comp_idea = _idea(
            "Cert Monitor Compliance",
            "monitor certificates for security compliance issues",
            category=IdeaCategory.COMPLIANCE,
        )
        await db.save_idea(sec_idea)
        await db.save_idea(comp_idea)

        total = await db.count_ideas()
        assert total == 2, "Same tagline in different categories should both save"

    @pytest.mark.asyncio
    async def test_dedup_ignores_rejected_ideas(self, db):
        """Near-duplicate check should skip rejected ideas (allow re-proposal)."""
        original = _idea(
            "Old Scanner",
            "scan infrastructure for configuration drift",
            category=IdeaCategory.AUTOMATION,
        )
        await db.save_idea(original)
        await db.update_idea_status(original.id, "rejected")

        new_version = _idea(
            "New Scanner",
            "scan infrastructure for configuration drift — improved version",
            category=IdeaCategory.AUTOMATION,
        )
        await db.save_idea(new_version)

        active = await db.list_ideas(category=IdeaCategory.AUTOMATION, limit=100)
        non_rejected = [i for i in active if i.status != "rejected"]
        assert len(non_rejected) == 1
        assert non_rejected[0].name == "New Scanner"

    @pytest.mark.asyncio
    async def test_si_dedup_still_works(self, db):
        """Self-improvement dedup should continue working under universal dedup."""
        original = _idea(
            "Dashboard Fix V1",
            "dashboard UX improvements and accessibility gaps — tailored for reliability",
            category=IdeaCategory.SELF_IMPROVEMENT,
        )
        await db.save_idea(original)

        near_dup = _idea(
            "Dashboard Fix V2",
            "dashboard UX improvements and accessibility gaps — tailored for testing",
            category=IdeaCategory.SELF_IMPROVEMENT,
        )
        await db.save_idea(near_dup)

        all_si = await db.list_ideas(category=IdeaCategory.SELF_IMPROVEMENT, limit=100)
        assert len(all_si) == 1


# ---------------------------------------------------------------------------
# 3. Filtered idea audit trail — storage
# ---------------------------------------------------------------------------


class TestFilteredIdeaStorage:
    """filtered_ideas table stores ideas rejected by dedup or quality review."""

    @pytest.mark.asyncio
    async def test_save_filtered_idea(self, db):
        """save_filtered_idea persists to the filtered_ideas table."""
        fi = FilteredIdea(
            idea_name="Cert Scanner Pro",
            idea_tagline="scan certs for issues",
            idea_category=IdeaCategory.SECURITY_TOOL,
            filter_reason="duplicate:tagline_similarity:0.85",
            original_idea_json='{"name": "Cert Scanner Pro"}',
            similar_to_id="existing123",
        )
        result = await db.save_filtered_idea(fi)
        assert result.id == fi.id

    @pytest.mark.asyncio
    async def test_get_filtered_ideas_all(self, db):
        """get_filtered_ideas returns all filtered ideas when no filters specified."""
        for i in range(3):
            fi = FilteredIdea(
                idea_name=f"Filtered {i}",
                idea_tagline=f"tagline {i}",
                idea_category=IdeaCategory.SECURITY_TOOL,
                filter_reason="duplicate:content_hash",
                original_idea_json="{}",
            )
            await db.save_filtered_idea(fi)

        results = await db.get_filtered_ideas()
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_get_filtered_ideas_by_category(self, db):
        """get_filtered_ideas can filter by category."""
        fi1 = FilteredIdea(
            idea_name="Sec Idea",
            idea_tagline="sec",
            idea_category=IdeaCategory.SECURITY_TOOL,
            filter_reason="duplicate:content_hash",
            original_idea_json="{}",
        )
        fi2 = FilteredIdea(
            idea_name="PQC Idea",
            idea_tagline="pqc",
            idea_category=IdeaCategory.PQC_CRYPTOGRAPHY,
            filter_reason="duplicate:content_hash",
            original_idea_json="{}",
        )
        await db.save_filtered_idea(fi1)
        await db.save_filtered_idea(fi2)

        results = await db.get_filtered_ideas(category=IdeaCategory.SECURITY_TOOL)
        assert len(results) == 1
        assert results[0].idea_category == IdeaCategory.SECURITY_TOOL

    @pytest.mark.asyncio
    async def test_get_filtered_ideas_by_reason(self, db):
        """get_filtered_ideas can filter by reason prefix."""
        fi1 = FilteredIdea(
            idea_name="Dup Idea",
            idea_tagline="dup",
            idea_category=IdeaCategory.AUTOMATION,
            filter_reason="duplicate:tagline_similarity:0.90",
            original_idea_json="{}",
        )
        fi2 = FilteredIdea(
            idea_name="Quality Idea",
            idea_tagline="quality",
            idea_category=IdeaCategory.AUTOMATION,
            filter_reason="quality:buzzwords",
            original_idea_json="{}",
        )
        await db.save_filtered_idea(fi1)
        await db.save_filtered_idea(fi2)

        dup_results = await db.get_filtered_ideas(reason_prefix="duplicate")
        assert len(dup_results) == 1
        assert "duplicate" in dup_results[0].filter_reason

        quality_results = await db.get_filtered_ideas(reason_prefix="quality")
        assert len(quality_results) == 1
        assert "quality" in quality_results[0].filter_reason


# ---------------------------------------------------------------------------
# 4. save_idea logs to filtered_ideas on dedup
# ---------------------------------------------------------------------------


class TestSaveIdeaAuditTrail:
    """save_idea should log filtered ideas to the audit trail."""

    @pytest.mark.asyncio
    async def test_content_hash_dup_logged(self, db):
        """When content_hash dedup blocks an idea, it's logged to filtered_ideas."""
        idea1 = _idea("Original", "original tagline", content_hash="hash_same")
        await db.save_idea(idea1)

        idea2 = _idea("Duplicate", "duplicate tagline", content_hash="hash_same")
        await db.save_idea(idea2)

        filtered = await db.get_filtered_ideas()
        assert len(filtered) == 1
        assert filtered[0].idea_name == "Duplicate"
        assert filtered[0].filter_reason == "duplicate:content_hash"

    @pytest.mark.asyncio
    async def test_tagline_dup_logged_with_score(self, db):
        """When tagline dedup blocks an idea, the similarity score is in the reason."""
        original = _idea(
            "Scanner V1",
            "scan infrastructure for drift — enterprise",
            category=IdeaCategory.OBSERVABILITY,
        )
        await db.save_idea(original)

        near_dup = _idea(
            "Scanner V2",
            "scan infrastructure for drift — community",
            category=IdeaCategory.OBSERVABILITY,
        )
        await db.save_idea(near_dup)

        filtered = await db.get_filtered_ideas()
        assert len(filtered) == 1
        assert filtered[0].filter_reason.startswith("duplicate:tagline_similarity:")
        # Score should be included as a float
        score_str = filtered[0].filter_reason.split(":")[-1]
        score = float(score_str)
        assert score >= 0.7

    @pytest.mark.asyncio
    async def test_tagline_dup_logged_with_similar_to_id(self, db):
        """Filtered idea should reference the ID of the idea it matched against."""
        original = _idea(
            "Monitor V1",
            "monitor kubernetes pods for resource exhaustion — production",
            category=IdeaCategory.DEVOPS_TOOLING,
        )
        await db.save_idea(original)

        near_dup = _idea(
            "Monitor V2",
            "monitor kubernetes pods for resource exhaustion — staging",
            category=IdeaCategory.DEVOPS_TOOLING,
        )
        await db.save_idea(near_dup)

        filtered = await db.get_filtered_ideas()
        assert len(filtered) == 1
        assert filtered[0].similar_to_id == original.id


# ---------------------------------------------------------------------------
# 5. Dedup stats
# ---------------------------------------------------------------------------


class TestDedupStats:
    """get_dedup_stats returns counts by filter_reason and category."""

    @pytest.mark.asyncio
    async def test_dedup_stats_empty(self, db):
        """Stats on empty table return zeroes."""
        stats = await db.get_dedup_stats()
        assert stats["total_filtered"] == 0
        assert stats["by_reason"] == {}
        assert stats["by_category"] == {}

    @pytest.mark.asyncio
    async def test_dedup_stats_counts(self, db):
        """Stats correctly count by reason and category."""
        entries = [
            ("A", IdeaCategory.SECURITY_TOOL, "duplicate:content_hash"),
            ("B", IdeaCategory.SECURITY_TOOL, "duplicate:tagline_similarity:0.85"),
            ("C", IdeaCategory.PQC_CRYPTOGRAPHY, "duplicate:content_hash"),
            ("D", IdeaCategory.AUTOMATION, "quality:buzzwords"),
        ]
        for name, cat, reason in entries:
            fi = FilteredIdea(
                idea_name=name,
                idea_tagline="t",
                idea_category=cat,
                filter_reason=reason,
                original_idea_json="{}",
            )
            await db.save_filtered_idea(fi)

        stats = await db.get_dedup_stats()
        assert stats["total_filtered"] == 4
        # by_reason groups by prefix before second colon
        assert stats["by_reason"]["duplicate:content_hash"] == 2
        assert stats["by_reason"]["duplicate:tagline_similarity"] == 1
        assert stats["by_reason"]["quality:buzzwords"] == 1
        # by_category
        assert stats["by_category"]["security-tool"] == 2
        assert stats["by_category"]["pqc-cryptography"] == 1
        assert stats["by_category"]["automation"] == 1
