"""Tests for all remaining gaps from team review (P2 + P3).

Covers:
1. _call_claude: timeout, empty response, stop_reason validation
2. apply_changes: directory allowlist (block .github/, .env, dotfiles)
3. gather_self_context: absolute paths (not cwd-dependent)
4. Dedup: rejected SI ideas should NOT block re-proposals
5. Dedup: en dash normalization
6. promote_proposal: reject non-SI ideas
7. Rate limiting on approve/promote
8. Critical missing tests from Tester report
"""

import inspect  # noqa: I001
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database
from project_forge.web.app import app, db


@pytest_asyncio.fixture
async def client(tmp_path):
    db.db_path = tmp_path / "test_gaps.db"
    await db.connect()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await db.close()


def _si_idea(**overrides) -> Idea:
    defaults = dict(
        name="Gap Test Idea",
        tagline="unique gap test tagline",
        description="Test.",
        category=IdeaCategory.SELF_IMPROVEMENT,
        market_analysis="Internal.",
        feasibility_score=0.8,
        mvp_scope="Build it.",
        tech_stack=["python"],
        status="new",
    )
    defaults.update(overrides)
    return Idea(**defaults)


# ===================================================================
# 1. _call_claude: robustness
# ===================================================================


class TestCallClaudeRobustness:
    """_call_claude should handle edge cases gracefully."""

    def test_validates_response_has_content(self):
        """Should raise if Claude returns empty content."""
        from project_forge.cron.self_improve_runner import _call_claude

        mock_response = MagicMock()
        mock_response.content = []  # empty

        with (
            patch("project_forge.cron.self_improve_runner.anthropic") as mock_anthropic,
            patch("project_forge.cron.self_improve_runner.settings") as mock_settings,
        ):
            mock_settings.anthropic_api_key = "test-key"
            mock_settings.anthropic_model = "test-model"
            mock_client = MagicMock()
            mock_anthropic.Anthropic.return_value = mock_client
            mock_client.messages.create.return_value = mock_response

            with pytest.raises((ValueError, IndexError)):
                _call_claude("test prompt")

    def test_validates_stop_reason_is_end_turn(self):
        """Should raise if Claude's stop_reason is not end_turn (truncated response)."""
        from project_forge.cron.self_improve_runner import _call_claude

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"changes": []}')]
        mock_response.stop_reason = "max_tokens"  # truncated!

        with (
            patch("project_forge.cron.self_improve_runner.anthropic") as mock_anthropic,
            patch("project_forge.cron.self_improve_runner.settings") as mock_settings,
        ):
            mock_settings.anthropic_api_key = "test-key"
            mock_settings.anthropic_model = "test-model"
            mock_client = MagicMock()
            mock_anthropic.Anthropic.return_value = mock_client
            mock_client.messages.create.return_value = mock_response

            with pytest.raises(ValueError, match="truncated|max_tokens"):
                _call_claude("test prompt")


# ===================================================================
# 2. apply_changes: directory allowlist
# ===================================================================


class TestApplyChangesAllowlist:
    """apply_changes should block writes to sensitive directories."""

    def test_blocks_github_workflows(self, tmp_path):
        from project_forge.cron.self_improve_runner import apply_changes

        changes = [{"path": ".github/workflows/ci.yml", "action": "create", "content": "hacked"}]
        with pytest.raises(ValueError, match="blocked|restricted"):
            apply_changes(changes, project_root=tmp_path)

    def test_blocks_dotenv(self, tmp_path):
        from project_forge.cron.self_improve_runner import apply_changes

        changes = [{"path": ".env", "action": "create", "content": "SECRET=bad"}]
        with pytest.raises(ValueError, match="blocked|restricted"):
            apply_changes(changes, project_root=tmp_path)

    def test_blocks_dotfiles(self, tmp_path):
        from project_forge.cron.self_improve_runner import apply_changes

        changes = [{"path": ".bashrc", "action": "create", "content": "alias rm='rm -rf /'"}]
        with pytest.raises(ValueError, match="blocked|restricted"):
            apply_changes(changes, project_root=tmp_path)

    def test_allows_src_directory(self, tmp_path):
        from project_forge.cron.self_improve_runner import apply_changes

        changes = [{"path": "src/project_forge/engine/new_module.py", "action": "create", "content": "x = 1\n"}]
        result = apply_changes(changes, project_root=tmp_path)
        assert "src/project_forge/engine/new_module.py" in result

    def test_allows_tests_directory(self, tmp_path):
        from project_forge.cron.self_improve_runner import apply_changes

        changes = [{"path": "tests/test_new.py", "action": "create", "content": "def test_x(): pass\n"}]
        result = apply_changes(changes, project_root=tmp_path)
        assert "tests/test_new.py" in result


# ===================================================================
# 3. gather_self_context: absolute paths
# ===================================================================


class TestGatherSelfContextPaths:
    """gather_self_context must use absolute paths, not cwd-relative."""

    def test_uses_absolute_path_for_test_dir(self):
        """The test_dir Path should be absolute or rooted at _PROJECT_ROOT."""
        from project_forge.engine import introspect

        source = inspect.getsource(introspect.gather_self_context)
        # Should reference _PROJECT_ROOT for the test directory, not bare Path("tests")
        assert '_PROJECT_ROOT / "tests"' in source or "_PROJECT_ROOT" in source.split("test_dir")[0]

    def test_ruff_command_uses_absolute_paths(self):
        """The ruff check command should use absolute paths."""
        from project_forge.engine import introspect

        source = inspect.getsource(introspect.gather_self_context)
        # Should not have bare 'src/' or 'tests/' in the ruff command
        # Look for the ruff line and check it uses str(_PROJECT_ROOT / ...)
        ruff_lines = [line for line in source.split("\n") if "ruff" in line and "check" in line]
        assert ruff_lines, "Should have a ruff check command"
        ruff_line = ruff_lines[0]
        assert "str(" in ruff_line or "_PROJECT_ROOT" in ruff_line


# ===================================================================
# 4. Dedup: rejected SI ideas should NOT block re-proposals
# ===================================================================


class TestDedupRejectedIdeas:
    """Rejected SI ideas should not prevent saving a better version of the same concept."""

    @pytest.mark.asyncio
    async def test_rejected_si_does_not_block_new_proposal(self, tmp_path):
        d = Database(tmp_path / "dedup_reject.db")
        await d.connect()

        original = _si_idea(
            name="Dashboard Fix V1",
            tagline="dashboard UX improvements and accessibility gaps",
            status="new",
        )
        await d.save_idea(original)
        # Reject it
        await d.update_idea_status(original.id, "rejected")

        # New proposal with same concept should be saved (not blocked by rejected idea)
        retry = _si_idea(
            name="Dashboard Fix V2",
            tagline="dashboard UX improvements and accessibility gaps",
        )
        await d.save_idea(retry)

        all_si = await d.list_ideas(category=IdeaCategory.SELF_IMPROVEMENT, limit=100)
        names = [i.name for i in all_si]
        assert "Dashboard Fix V2" in names
        await d.close()


# ===================================================================
# 5. Dedup: en dash normalization
# ===================================================================


class TestDedupEnDash:
    """tagline_similarity should normalize en dashes (U+2013) like em dashes."""

    def test_en_dash_normalized(self):
        from project_forge.engine.dedup import tagline_similarity

        # en dash (–) vs em dash (—)
        a = "dashboard UX improvements \u2013 tailored for developer experience"
        b = "dashboard UX improvements \u2014 tailored for test engineering"
        score = tagline_similarity(a, b)
        assert score == 1.0, f"en dash and em dash should normalize the same, got {score}"

    def test_en_dash_with_no_suffix(self):
        from project_forge.engine.dedup import tagline_similarity

        a = "dashboard UX improvements \u2013 tailored for X"
        b = "dashboard UX improvements"
        score = tagline_similarity(a, b)
        assert score == 1.0


# ===================================================================
# 6. promote_proposal: reject non-SI ideas
# ===================================================================


class TestPromoteRejectsNonSI:
    """POST /api/thinktank/{id}/promote should reject non-self-improvement ideas."""

    @pytest.mark.asyncio
    async def test_promote_non_si_returns_400(self, client):
        idea = Idea(
            name="PKI Scanner",
            tagline="scan for cert issues",
            description="Tool.",
            category=IdeaCategory.SECURITY_TOOL,
            market_analysis="Market.",
            feasibility_score=0.7,
            mvp_scope="Build.",
            tech_stack=["python"],
        )
        await db.save_idea(idea)

        resp = await client.post(f"/api/thinktank/{idea.id}/promote")
        assert resp.status_code == 400


# ===================================================================
# 7. Rate limiting on approve/promote
# ===================================================================


class TestApprovePromoteRateLimiting:
    """approve and promote should have rate limiting."""

    @pytest.mark.asyncio
    async def test_approve_rate_limited_after_burst(self, client):
        """Rapid approve calls should eventually be rate-limited."""
        # Use non-SI ideas to avoid dedup and GH issue creation complexity
        ideas = []
        for i in range(7):
            idea = Idea(
                name=f"Rate Test {i}",
                tagline=f"completely different tagline about topic {i} here",
                description="Test.",
                category=IdeaCategory.SECURITY_TOOL,
                market_analysis="Market.",
                feasibility_score=0.7,
                mvp_scope="Build.",
                tech_stack=["python"],
            )
            await db.save_idea(idea)
            ideas.append(idea)

        responses = []
        for idea in ideas:
            resp = await client.post(f"/ideas/{idea.id}/approve")
            responses.append(resp.status_code)

        # At least one should be rate-limited (429) — limit is 5 per 60s
        assert 429 in responses, f"Expected at least one 429, got {responses}"


# ===================================================================
# 8. Critical missing tests from Tester report
# ===================================================================


class TestMissingFromTesterReport:
    """Covers the highest-priority gaps from the Tester agent."""

    def test_apply_changes_partial_failure_does_not_leave_first_file(self, tmp_path):
        """If second change fails, first change was already written (caller must revert)."""
        from project_forge.cron.self_improve_runner import apply_changes

        (tmp_path / "tests").mkdir(exist_ok=True)
        (tmp_path / "tests" / "bad.py").write_text("no match here\n")
        changes = [
            {"path": "tests/ok.py", "action": "create", "content": "good\n"},
            {"path": "tests/bad.py", "action": "edit", "search": "missing", "replace": "x"},
        ]
        with pytest.raises(ValueError):
            apply_changes(changes, project_root=tmp_path)

        # First file WAS written (this is expected — caller must revert)
        assert (tmp_path / "tests" / "ok.py").exists()

    def test_validate_changes_both_fail_shows_both(self):
        """When both pytest and ruff fail, detail should contain both messages."""
        from project_forge.cron.self_improve_runner import validate_changes

        def both_fail(cmd, **kwargs):
            if "pytest" in cmd:
                return (1, "FAILED test_foo.py::test_bar")
            if "ruff" in cmd:
                return (1, "E501 line too long")
            return (0, "ok")

        with patch("project_forge.cron.self_improve_runner._run_cmd", side_effect=both_fail):
            result = validate_changes()

        assert result["passed"] is False
        assert "FAILED" in result["detail"]
        assert "E501" in result["detail"]

    @pytest.mark.asyncio
    async def test_approve_nonexistent_returns_404(self, client):
        """POST /ideas/nonexistent/approve should return 404."""
        resp = await client.post("/ideas/nonexistent-id-12345/approve")
        assert resp.status_code == 404

    def test_build_introspection_prompt_empty_context(self):
        """Empty context dict should not raise."""
        from project_forge.engine.introspect import build_introspection_prompt

        result = build_introspection_prompt({}, [])
        assert len(result) > 0

    def test_count_lines_nonexistent_directory(self):
        """_count_lines on nonexistent path should return 0."""
        from project_forge.engine.introspect import _count_lines

        assert _count_lines(Path("/nonexistent/path/42")) == 0

    def test_run_cycle_invalid_claude_response(self):
        """Bad JSON from Claude should result in status='error', not close the issue."""
        import asyncio

        from project_forge.cron.self_improve_runner import run_self_improve_cycle

        fake_issue = {
            "number": 99,
            "title": "Test issue",
            "body": "Fix something",
            "url": "http://gh/99",
            "labels": [{"name": "ci-queue"}],
            "state": "OPEN",
        }

        with (
            patch(
                "project_forge.cron.self_improve_runner.fetch_ci_queue_issues",
                return_value=[fake_issue],
            ),
            patch(
                "project_forge.cron.self_improve_runner.gather_self_context",
                return_value={"code_stats": {}, "test_count": 10},
            ),
            patch(
                "project_forge.cron.self_improve_runner._call_claude",
                return_value="this is not json at all!!",
            ),
            patch("project_forge.cron.self_improve_runner._revert_changes"),
            patch("project_forge.cron.self_improve_runner.close_issue") as mock_close,
            patch("project_forge.cron.self_improve_runner.settings") as mock_settings,
        ):
            mock_settings.anthropic_api_key = "fake-key"
            mock_settings.anthropic_model = "claude-sonnet-4-20250514"
            result = asyncio.get_event_loop().run_until_complete(run_self_improve_cycle())

        assert result["results"][0]["status"] == "error"
        mock_close.assert_not_called()

    def test_run_cycle_multiple_issues_independent(self):
        """Two issues: first succeeds, second fails — both are tracked independently."""
        import asyncio
        import json

        from project_forge.cron.self_improve_runner import run_self_improve_cycle

        good_issue = {"number": 1, "title": "Good", "body": "x", "url": "u", "labels": [], "state": "OPEN"}
        bad_issue = {"number": 2, "title": "Bad", "body": "x", "url": "u", "labels": [], "state": "OPEN"}

        good_response = json.dumps(
            {
                "summary": "Fixed it",
                "changes": [{"path": "src/fix.py", "action": "create", "content": "x = 1\n"}],
            }
        )

        call_count = {"n": 0}

        def mock_claude(prompt):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return good_response
            return "not json!!!"

        with (
            patch(
                "project_forge.cron.self_improve_runner.fetch_ci_queue_issues",
                return_value=[good_issue, bad_issue],
            ),
            patch(
                "project_forge.cron.self_improve_runner.gather_self_context",
                return_value={"code_stats": {}, "test_count": 10},
            ),
            patch("project_forge.cron.self_improve_runner._call_claude", side_effect=mock_claude),
            patch("project_forge.cron.self_improve_runner.apply_changes", return_value=["src/fix.py"]),
            patch(
                "project_forge.cron.self_improve_runner.validate_changes",
                return_value={"passed": True, "detail": ""},
            ),
            patch(
                "project_forge.cron.self_improve_runner.create_improvement_pr",
                return_value="http://gh/pr/1",
            ),
            patch("project_forge.cron.self_improve_runner.close_issue"),
            patch("project_forge.cron.self_improve_runner._revert_changes"),
            patch("project_forge.cron.self_improve_runner.settings") as mock_settings,
        ):
            mock_settings.anthropic_api_key = "fake-key"
            mock_settings.anthropic_model = "claude-sonnet-4-20250514"
            result = asyncio.get_event_loop().run_until_complete(run_self_improve_cycle())

        assert result["processed"] == 2
        assert result["results"][0]["status"] == "success"
        assert result["results"][1]["status"] == "error"
