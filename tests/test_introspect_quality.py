"""Tests for introspection prompt quality and SI idea validation.

Bug: The introspection engine generates "self-improvement" ideas that are actually
proposals for new external projects ("Phase 1: build a CLI tool", "SaaS dashboard",
"market analysis shows demand"). Real self-improvement means: specific code changes
to THIS codebase (project-forge), referencing actual files, modules, or test gaps.

Fixes:
1. The prompt must anchor Claude to specific file/module references
2. Generated ideas must pass a validation gate that rejects new-project language
3. The mvp_scope must reference project-forge internals, not greenfield deliverables
"""

import pytest  # noqa: I001
from project_forge.models import Idea, IdeaCategory


# ===================================================================
# 1. Prompt quality: must demand concrete code references
# ===================================================================


class TestIntrospectionPromptQuality:
    """The introspection prompt must force Claude to think about THIS codebase."""

    def test_prompt_mentions_specific_files(self):
        """Prompt should list actual project-forge file paths to anchor Claude."""
        from project_forge.engine.introspect import build_introspection_prompt

        prompt = build_introspection_prompt(
            {"open_issues": [], "recent_commits": [], "test_count": 10, "lint_status": "clean", "code_stats": {}},
            [],
        )
        # Must mention actual project structure
        assert "src/project_forge/" in prompt

    def test_prompt_forbids_new_project_language(self):
        """Prompt should explicitly tell Claude NOT to propose new external projects."""
        from project_forge.engine.introspect import build_introspection_prompt

        prompt = build_introspection_prompt(
            {"open_issues": [], "recent_commits": [], "test_count": 10, "lint_status": "clean", "code_stats": {}},
            [],
        )
        prompt_lower = prompt.lower()
        assert (
            "not a new project" in prompt_lower
            or "do not propose" in prompt_lower
            or "existing code" in prompt_lower
        )

    def test_prompt_requires_file_paths_in_response(self):
        """Prompt should require the response to include specific file paths to change."""
        from project_forge.engine.introspect import build_introspection_prompt

        prompt = build_introspection_prompt(
            {"open_issues": [], "recent_commits": [], "test_count": 10, "lint_status": "clean", "code_stats": {}},
            [],
        )
        # The JSON format should include a field for files_to_change or similar
        assert "files_to_change" in prompt or "affected_files" in prompt

    def test_prompt_includes_directory_listing(self):
        """Prompt should include the actual file tree so Claude knows what exists."""
        from project_forge.engine.introspect import build_introspection_prompt

        context = {
            "open_issues": [],
            "recent_commits": [],
            "test_count": 10,
            "lint_status": "clean",
            "code_stats": {"src": 2000, "tests": 1000},
            "file_tree": ["src/project_forge/web/routes.py", "src/project_forge/engine/introspect.py"],
        }
        prompt = build_introspection_prompt(context, [])
        # Should include the file tree in the prompt
        assert "routes.py" in prompt or "file_tree" in prompt.lower()


# ===================================================================
# 2. SI idea validation: reject new-project proposals
# ===================================================================


class TestSelfImprovementValidation:
    """validate_self_improvement rejects ideas that are really new-project proposals."""

    def test_rejects_phase_language(self):
        """Ideas with 'Phase 1', 'Phase 2' in description are new-project proposals."""
        from project_forge.engine.introspect import validate_self_improvement

        idea = Idea(
            name="Runner Monitor",
            tagline="self-hosted runner health monitoring",
            description="Phase 1 (Weeks 1-2): Core engine. Phase 2: Web dashboard.",
            category=IdeaCategory.SELF_IMPROVEMENT,
            market_analysis="Market demand.",
            feasibility_score=0.6,
            mvp_scope="Build a CLI tool.",
            tech_stack=["python"],
        )
        assert validate_self_improvement(idea) is False

    def test_rejects_saas_language(self):
        """Ideas mentioning SaaS, multi-tenant, enterprise SSO are new projects."""
        from project_forge.engine.introspect import validate_self_improvement

        idea = Idea(
            name="Auth Service",
            tagline="enterprise auth",
            description="Multi-tenant SaaS with enterprise SSO integration.",
            category=IdeaCategory.SELF_IMPROVEMENT,
            market_analysis="Growing market.",
            feasibility_score=0.5,
            mvp_scope="Build SSO module.",
            tech_stack=["python"],
        )
        assert validate_self_improvement(idea) is False

    def test_rejects_market_demand_language(self):
        """mvp_scope mentioning 'market', 'customers', 'adoption' is a new project."""
        from project_forge.engine.introspect import validate_self_improvement

        idea = Idea(
            name="Scanner Tool",
            tagline="vulnerability scanner",
            description="Teams are willing to pay for tools that save them time.",
            category=IdeaCategory.SELF_IMPROVEMENT,
            market_analysis="Competitive landscape is sparse.",
            feasibility_score=0.7,
            mvp_scope="Ship v1 to early adopters.",
            tech_stack=["python"],
        )
        assert validate_self_improvement(idea) is False

    def test_accepts_genuine_self_improvement(self):
        """Ideas that reference project-forge internals should pass validation."""
        from project_forge.engine.introspect import validate_self_improvement

        idea = Idea(
            name="Rate Limit API",
            tagline="add rate limiting to forge API endpoints",
            description=(
                "The FastAPI routes in src/project_forge/web/routes.py lack rate limiting. "
                "Add slowapi middleware to prevent abuse of the generation endpoints."
            ),
            category=IdeaCategory.SELF_IMPROVEMENT,
            market_analysis="Prevents API abuse on the forge dashboard.",
            feasibility_score=0.85,
            mvp_scope="Add rate limiting to POST endpoints in routes.py.",
            tech_stack=["python", "fastapi"],
        )
        assert validate_self_improvement(idea) is True

    def test_accepts_test_gap_improvement(self):
        """Ideas about adding tests to existing modules should pass."""
        from project_forge.engine.introspect import validate_self_improvement

        idea = Idea(
            name="Test Coverage Gaps",
            tagline="add missing tests for the scaffold module",
            description=(
                "The scaffold/builder.py module has no dedicated test file. "
                "Add tests for build_scaffold_spec and render_scaffold."
            ),
            category=IdeaCategory.SELF_IMPROVEMENT,
            market_analysis="Improves reliability of the scaffolding pipeline.",
            feasibility_score=0.9,
            mvp_scope="Create tests/test_scaffold_builder.py with unit tests.",
            tech_stack=["python", "pytest"],
        )
        assert validate_self_improvement(idea) is True


# ===================================================================
# 3. gather_self_context includes file tree
# ===================================================================


class TestGatherContextIncludesFileTree:
    """gather_self_context should return a file_tree for the prompt."""

    def test_context_has_file_tree(self):
        from project_forge.engine.introspect import gather_self_context

        ctx = gather_self_context()
        assert "file_tree" in ctx
        assert isinstance(ctx["file_tree"], list)
        assert len(ctx["file_tree"]) > 0

    def test_file_tree_includes_key_modules(self):
        from project_forge.engine.introspect import gather_self_context

        ctx = gather_self_context()
        tree_str = " ".join(ctx["file_tree"])
        assert "routes.py" in tree_str
        assert "introspect.py" in tree_str


# ===================================================================
# 4. Introspection runner uses validation
# ===================================================================


class TestIntrospectionRunnerValidation:
    """The runner should reject ideas that fail validation."""

    @pytest.mark.asyncio
    async def test_runner_rejects_invalid_si_idea(self):
        """If the generated idea fails validation, the runner should retry or skip."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from project_forge.cron.introspect_runner import run_introspect_cycle
        from project_forge.storage.db import Database

        mock_db = AsyncMock(spec=Database)
        mock_db.list_ideas = AsyncMock(return_value=[])
        mock_db.save_idea = AsyncMock()

        # Generate a bad idea (new-project proposal)
        bad_idea = Idea(
            name="New Scanner Tool",
            tagline="vulnerability scanner for enterprises",
            description="Phase 1: Build CLI. Phase 2: SaaS dashboard.",
            category=IdeaCategory.SELF_IMPROVEMENT,
            market_analysis="Growing market demand.",
            feasibility_score=0.6,
            mvp_scope="Ship to early adopters.",
            tech_stack=["python"],
        )
        mock_gen = MagicMock()
        mock_gen.generate = AsyncMock(return_value=bad_idea)

        with patch(
            "project_forge.cron.introspect_runner.gather_self_context",
            return_value={
                "open_issues": [],
                "recent_commits": [],
                "test_count": 10,
                "lint_status": "clean",
                "code_stats": {"src": 1000, "tests": 500},
                "file_tree": ["src/project_forge/web/routes.py"],
            },
        ):
            result = await run_introspect_cycle(mock_db, mock_gen)

        # Should NOT save an invalid idea
        mock_db.save_idea.assert_not_called()
        assert result is None
