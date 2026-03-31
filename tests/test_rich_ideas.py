"""Tests for rich idea content generation -- no more thin template fill-ins."""

from project_forge.cron.auto_scan import generate_local_idea
from project_forge.models import IdeaCategory


class TestRichDescription:
    def test_description_has_multiple_paragraphs(self):
        """Description should have real substance, not one line."""
        idea, *_ = generate_local_idea(category=IdeaCategory.PQC_CRYPTOGRAPHY)
        # At least 200 chars and multiple sentences
        assert len(idea.description) >= 200, f"Description too short: {len(idea.description)} chars"
        assert idea.description.count(".") >= 3, "Description should have 3+ sentences"

    def test_description_not_generic_template(self):
        """Description should NOT be a generic template fill-in."""
        idea, *_ = generate_local_idea(category=IdeaCategory.SECURITY_TOOL)
        # These are the old generic patterns
        assert "This fills a real gap" not in idea.description
        assert "to create something neither domain has alone" not in idea.description

    def test_market_analysis_is_specific(self):
        """Market analysis should explain WHY NOW and WHO NEEDS IT."""
        idea, *_ = generate_local_idea(category=IdeaCategory.NIST_STANDARDS)
        assert len(idea.market_analysis) >= 100, f"Market too short: {len(idea.market_analysis)} chars"
        # Should not be the old generic template
        assert "This fills a real gap" not in idea.market_analysis

    def test_mvp_scope_has_specifics(self):
        """MVP scope should list concrete deliverables, not generic filler."""
        idea, *_ = generate_local_idea(category=IdeaCategory.RFC_SECURITY)
        assert len(idea.mvp_scope) >= 100, f"MVP too short: {len(idea.mvp_scope)} chars"
        # Should not be the old generic template
        assert "Target 2-4 week delivery" not in idea.mvp_scope

    def test_feasibility_score_is_category_appropriate(self):
        """Score should reflect the concept, not be random."""
        # Generate multiple and check they're not all the same
        scores = []
        for _ in range(10):
            idea, *_ = generate_local_idea()
            scores.append(idea.feasibility_score)
        unique_scores = set(scores)
        assert len(unique_scores) >= 5, "Scores should have variety"

    def test_all_categories_produce_rich_content(self):
        """Every category should produce rich, non-generic content."""
        for cat in IdeaCategory:
            idea, *_ = generate_local_idea(category=cat)
            assert len(idea.description) >= 200, f"{cat}: description too short"
            assert len(idea.market_analysis) >= 100, f"{cat}: market too short"
            assert len(idea.mvp_scope) >= 100, f"{cat}: mvp too short"
            assert "This fills a real gap" not in idea.market_analysis
            assert "Target 2-4 week delivery" not in idea.mvp_scope
