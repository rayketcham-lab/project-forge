"""Tests for the universal quality review gate.

Every idea — SI, regular, cross-category, expanded — must pass a quality review
before being saved. The review checks:
1. Logistics: Is the description specific enough? Does it have actionable scope?
2. Practicality: Does it make sense for the project category?
3. Benefit: Will this actually help? Is it a real concept or buzzword soup?

For SI ideas specifically: must reference project-forge files, not propose new projects.
"""

from project_forge.models import Idea, IdeaCategory


def _idea(**overrides) -> Idea:
    defaults = dict(
        name="Test Idea",
        tagline="A useful improvement",
        description="This is a concrete, specific improvement with clear scope.",
        category=IdeaCategory.SECURITY_TOOL,
        market_analysis="Fills a real gap in security tooling.",
        feasibility_score=0.8,
        mvp_scope="Build a CLI scanner that checks X.509 certificate chains.",
        tech_stack=["python"],
    )
    defaults.update(overrides)
    return Idea(**defaults)


# ===================================================================
# 1. ReviewResult structure
# ===================================================================


class TestReviewResult:
    """review_idea returns a structured result with passed, score, and reasons."""

    def test_returns_review_result(self):
        from project_forge.engine.quality_review import review_idea

        result = review_idea(_idea())
        assert hasattr(result, "passed")
        assert hasattr(result, "score")
        assert hasattr(result, "reasons")
        assert isinstance(result.passed, bool)
        assert isinstance(result.score, float)
        assert isinstance(result.reasons, list)


# ===================================================================
# 2. Rejects low-quality ideas (all categories)
# ===================================================================


class TestRejectsLowQuality:
    """review_idea should reject vague, buzzword-heavy, or empty ideas."""

    def test_rejects_empty_description(self):
        from project_forge.engine.quality_review import review_idea

        idea = _idea(description="")
        result = review_idea(idea)
        assert result.passed is False

    def test_rejects_very_short_description(self):
        from project_forge.engine.quality_review import review_idea

        idea = _idea(description="Do stuff.")
        result = review_idea(idea)
        assert result.passed is False

    def test_rejects_buzzword_soup_description(self):
        """Descriptions that are all buzzwords with no substance should fail."""
        from project_forge.engine.quality_review import review_idea

        idea = _idea(
            description=(
                "Leveraging cutting-edge AI-driven blockchain synergies to "
                "disrupt the paradigm shift in next-generation cloud-native "
                "microservice orchestration platforms."
            ),
        )
        result = review_idea(idea)
        assert result.passed is False

    def test_rejects_vague_mvp_scope(self):
        from project_forge.engine.quality_review import review_idea

        idea = _idea(mvp_scope="Build something useful.")
        result = review_idea(idea)
        assert result.passed is False

    def test_accepts_concrete_idea(self):
        from project_forge.engine.quality_review import review_idea

        idea = _idea(
            description=(
                "X.509 certificate chain validation is error-prone when done manually. "
                "This tool parses PEM/DER certificates, checks expiry dates, validates "
                "the chain of trust, and flags common misconfigurations like missing "
                "intermediates or weak signature algorithms."
            ),
            mvp_scope=(
                "CLI tool that reads a PEM file, validates the chain, and outputs "
                "a JSON report with findings. Support RSA and ECDSA certificates."
            ),
        )
        result = review_idea(idea)
        assert result.passed is True


# ===================================================================
# 3. SI-specific: rejects new-project proposals
# ===================================================================


class TestSISpecificReview:
    """SI ideas must reference project-forge internals, not propose new projects."""

    def test_rejects_phase_language(self):
        from project_forge.engine.quality_review import review_idea

        idea = _idea(
            category=IdeaCategory.SELF_IMPROVEMENT,
            description="Phase 1 (Weeks 1-2): Build CLI. Phase 2: Web dashboard.",
            mvp_scope="Ship v1 to early adopters.",
        )
        result = review_idea(idea)
        assert result.passed is False

    def test_rejects_saas_language(self):
        from project_forge.engine.quality_review import review_idea

        idea = _idea(
            category=IdeaCategory.SELF_IMPROVEMENT,
            description="Multi-tenant SaaS with enterprise SSO.",
            mvp_scope="Deploy to AWS.",
        )
        result = review_idea(idea)
        assert result.passed is False

    def test_accepts_genuine_code_improvement(self):
        from project_forge.engine.quality_review import review_idea

        idea = _idea(
            category=IdeaCategory.SELF_IMPROVEMENT,
            description=(
                "The FastAPI routes in src/project_forge/web/routes.py have no "
                "request logging. Add structured logging with correlation IDs "
                "to every route handler for better observability."
            ),
            mvp_scope=(
                "Add a logging middleware in src/project_forge/web/app.py. "
                "Create tests/test_request_logging.py with integration tests."
            ),
        )
        result = review_idea(idea)
        assert result.passed is True


# ===================================================================
# 4. Score reflects quality
# ===================================================================


class TestReviewScore:
    """The score should be higher for better ideas."""

    def test_concrete_idea_scores_higher_than_vague(self):
        from project_forge.engine.quality_review import review_idea

        concrete = _idea(
            description=(
                "Certificate transparency log monitoring tool that watches CT logs "
                "for unauthorized certificates issued for monitored domains. Uses "
                "the Merkle tree audit proof API to verify log consistency."
            ),
            mvp_scope="CLI tool querying CT log APIs, outputting alerts as JSON.",
        )
        vague = _idea(
            description="A tool that does security stuff for certificates.",
            mvp_scope="Build it and ship it.",
        )
        concrete_result = review_idea(concrete)
        vague_result = review_idea(vague)
        assert concrete_result.score > vague_result.score


# ===================================================================
# 5. Integration: runners use review_idea before saving
# ===================================================================


class TestRunnerIntegration:
    """All runners must call review_idea before db.save_idea."""

    def test_scheduler_calls_review(self):
        """scheduler.py should reference review_idea."""
        import inspect

        from project_forge.cron import scheduler

        source = inspect.getsource(scheduler)
        assert "review_idea" in source

    def test_horizontal_calls_review(self):
        """horizontal.py should reference review_idea."""
        import inspect

        from project_forge.cron import horizontal

        source = inspect.getsource(horizontal)
        assert "review_idea" in source

    def test_introspect_runner_calls_review(self):
        """introspect_runner.py should reference review_idea."""
        import inspect

        from project_forge.cron import introspect_runner

        source = inspect.getsource(introspect_runner)
        assert "review_idea" in source

    def test_auto_scan_calls_review(self):
        """auto_scan.py should reference review_idea."""
        import inspect

        from project_forge.cron import auto_scan

        source = inspect.getsource(auto_scan)
        assert "review_idea" in source
