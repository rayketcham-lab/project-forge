"""Tests for the self-introspection engine."""

from unittest.mock import MagicMock, patch

from project_forge.models import Idea, IdeaCategory


class TestGatherSelfContext:
    """Tests for gather_self_context()."""

    def test_gather_self_context_returns_expected_keys(self):
        """Verify the returned dict contains all required keys."""
        from project_forge.engine.introspect import gather_self_context

        mock_issues_result = MagicMock()
        mock_issues_result.returncode = 0
        mock_issues_result.stdout = (
            '[{"number": 1, "title": "Fix login bug", "labels": [], "url": "https://github.com/x/y/issues/1"}]'
        )

        mock_git_result = MagicMock()
        mock_git_result.returncode = 0
        mock_git_result.stdout = "abc1234 feat: add new feature\ndef5678 fix: resolve crash\n"

        mock_lint_result = MagicMock()
        mock_lint_result.returncode = 0
        mock_lint_result.stdout = "Found 0 errors.\n"

        def fake_run(cmd, **kwargs):
            if "gh" in cmd:
                return mock_issues_result
            if "git" in cmd:
                return mock_git_result
            if "ruff" in cmd:
                return mock_lint_result
            return MagicMock(returncode=0, stdout="")

        with patch("subprocess.run", side_effect=fake_run):
            with patch("project_forge.engine.introspect.Path") as mock_path:
                mock_path.return_value.glob.return_value = [
                    MagicMock(),
                    MagicMock(),
                    MagicMock(),
                ]
                ctx = gather_self_context()

        assert "open_issues" in ctx
        assert "recent_commits" in ctx
        assert "test_count" in ctx
        assert "lint_status" in ctx
        assert "code_stats" in ctx

    def test_gather_self_context_handles_gh_failure(self):
        """When gh fails, open_issues should be empty but function must not crash."""
        from project_forge.engine.introspect import gather_self_context

        def fake_run(cmd, **kwargs):
            if "gh" in cmd:
                raise FileNotFoundError("gh not found")
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = ""
            return mock

        with patch("subprocess.run", side_effect=fake_run):
            with patch("project_forge.engine.introspect.Path") as mock_path:
                mock_path.return_value.glob.return_value = []
                ctx = gather_self_context()

        assert ctx["open_issues"] == []

    def test_gather_self_context_handles_git_failure(self):
        """When git fails, recent_commits should be empty but function must not crash."""
        from project_forge.engine.introspect import gather_self_context

        def fake_run(cmd, **kwargs):
            if "git" in cmd:
                raise FileNotFoundError("git not found")
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = "[]"
            return mock

        with patch("subprocess.run", side_effect=fake_run):
            with patch("project_forge.engine.introspect.Path") as mock_path:
                mock_path.return_value.glob.return_value = []
                ctx = gather_self_context()

        assert ctx["recent_commits"] == []


class TestBuildIntrospectionPrompt:
    """Tests for build_introspection_prompt()."""

    def _make_context(self) -> dict:
        return {
            "open_issues": [
                {"number": 42, "title": "Add rate limiting to API", "labels": [], "url": "https://github.com/x/y/issues/42"},
                {"number": 7, "title": "Tests missing for scorer module", "labels": [], "url": "https://github.com/x/y/issues/7"},
            ],
            "recent_commits": [
                "abc1234 feat: add dashboard tabs",
                "def5678 fix: resolve crash in runner",
            ],
            "test_count": 15,
            "lint_status": "Found 0 errors.",
            "code_stats": {"src": 800, "tests": 400},
        }

    def test_build_introspection_prompt_includes_context(self):
        """Prompt must contain issue titles, commit messages, and test count."""
        from project_forge.engine.introspect import build_introspection_prompt

        ctx = self._make_context()
        prompt = build_introspection_prompt(ctx, recent_improvements=[])

        assert "Add rate limiting to API" in prompt
        assert "Tests missing for scorer module" in prompt
        assert "feat: add dashboard tabs" in prompt
        assert "15" in prompt

    def test_build_introspection_prompt_includes_recent_improvements(self):
        """Recent improvement names must appear in the prompt to avoid duplicates."""
        from project_forge.engine.introspect import build_introspection_prompt

        ctx = self._make_context()
        recent = ["Add test coverage for storage", "Improve CI pipeline caching"]
        prompt = build_introspection_prompt(ctx, recent_improvements=recent)

        assert "Add test coverage for storage" in prompt
        assert "Improve CI pipeline caching" in prompt

    def test_build_introspection_prompt_requests_self_improvement_category(self):
        """Prompt must instruct Claude to use the self-improvement category."""
        from project_forge.engine.introspect import build_introspection_prompt

        ctx = self._make_context()
        prompt = build_introspection_prompt(ctx, recent_improvements=[])

        assert "self-improvement" in prompt


class TestSelfImprovementCategory:
    """Tests that the IdeaCategory enum and Idea model support self-improvement."""

    def test_self_improvement_is_valid_category(self):
        """IdeaCategory must include the self-improvement value."""
        values = {c.value for c in IdeaCategory}
        assert "self-improvement" in values

    def test_idea_with_self_improvement_category(self):
        """An Idea with category=self-improvement must pass Pydantic validation."""
        idea = Idea(
            name="CI Coverage Enforcer",
            tagline="Automatically enforce test coverage thresholds in CI",
            description="Scans existing test suite, identifies untested modules, and opens GitHub issues.",
            category=IdeaCategory.SELF_IMPROVEMENT,
            market_analysis="Every project needs this.",
            feasibility_score=0.85,
            mvp_scope="CLI tool that runs pytest --cov and creates issues for gaps.",
            tech_stack=["python", "pytest", "github-api"],
        )
        assert idea.category == IdeaCategory.SELF_IMPROVEMENT
        assert idea.category.value == "self-improvement"
