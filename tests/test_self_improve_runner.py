"""Tests for the autonomous self-improvement runner.

The runner fetches open ci-queue issues, asks Claude to implement fixes,
applies changes, validates with tests+lint, creates a PR, and closes the issue.
All operations target project-forge itself — no external repo scaffolding.
"""

import json  # noqa: I001
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FAKE_ISSUE = {
    "number": 42,
    "title": "[Think Tank] Add rate limiting to API",
    "body": (
        "## Add rate limiting\n\n"
        "The API endpoints have no rate limiting. Add per-IP throttling "
        "to prevent abuse.\n\n"
        "**Feasibility:** 0.85\n**MVP Scope:** Add slowapi middleware"
    ),
    "url": "https://github.com/rayketcham-lab/project-forge/issues/42",
    "labels": [{"name": "ci-queue"}],
    "state": "OPEN",
}

FAKE_CLAUDE_RESPONSE = {
    "summary": "Added rate limiting middleware using slowapi",
    "changes": [
        {
            "path": "src/project_forge/web/app.py",
            "action": "edit",
            "search": "app = FastAPI()",
            "replace": ("from slowapi import Limiter\napp = FastAPI()\nlimiter = Limiter(key_func=get_remote_address)"),
        },
        {
            "path": "tests/test_rate_limit.py",
            "action": "create",
            "content": "# rate limit tests\n",
        },
    ],
}


# ---------------------------------------------------------------------------
# 1. Fetch ci-queue issues
# ---------------------------------------------------------------------------


class TestFetchCiQueueIssues:
    """fetch_ci_queue_issues returns open issues with the ci-queue label."""

    def test_returns_open_ci_queue_issues(self):
        from project_forge.cron.self_improve_runner import fetch_ci_queue_issues

        with patch("project_forge.cron.self_improve_runner._run_gh") as mock_gh:
            mock_gh.return_value = json.dumps([FAKE_ISSUE])
            issues = fetch_ci_queue_issues()

        assert len(issues) == 1
        assert issues[0]["number"] == 42

    def test_returns_empty_list_on_no_issues(self):
        from project_forge.cron.self_improve_runner import fetch_ci_queue_issues

        with patch("project_forge.cron.self_improve_runner._run_gh") as mock_gh:
            mock_gh.return_value = "[]"
            issues = fetch_ci_queue_issues()

        assert issues == []

    def test_returns_empty_on_gh_failure(self):
        from project_forge.cron.self_improve_runner import fetch_ci_queue_issues

        with patch("project_forge.cron.self_improve_runner._run_gh", side_effect=RuntimeError("gh failed")):
            issues = fetch_ci_queue_issues()

        assert issues == []


# ---------------------------------------------------------------------------
# 2. Build implementation prompt
# ---------------------------------------------------------------------------


class TestBuildImplementationPrompt:
    """build_implementation_prompt creates a prompt with issue + codebase context."""

    def test_includes_issue_title_and_body(self):
        from project_forge.cron.self_improve_runner import build_implementation_prompt

        prompt = build_implementation_prompt(FAKE_ISSUE, {"code_stats": {"src": 500}})

        assert "Add rate limiting" in prompt
        assert "slowapi middleware" in prompt

    def test_includes_codebase_context(self):
        from project_forge.cron.self_improve_runner import build_implementation_prompt

        context = {"code_stats": {"src": 1200, "tests": 800}, "test_count": 15}
        prompt = build_implementation_prompt(FAKE_ISSUE, context)

        assert "1200" in prompt or "src" in prompt

    def test_requests_structured_json_response(self):
        from project_forge.cron.self_improve_runner import build_implementation_prompt

        prompt = build_implementation_prompt(FAKE_ISSUE, {})

        assert "JSON" in prompt or "json" in prompt
        assert "changes" in prompt


# ---------------------------------------------------------------------------
# 3. Parse Claude's response into file changes
# ---------------------------------------------------------------------------


class TestParseImplementationResponse:
    """parse_implementation_response extracts structured changes from Claude output."""

    def test_parses_edit_action(self):
        from project_forge.cron.self_improve_runner import parse_implementation_response

        raw = json.dumps(FAKE_CLAUDE_RESPONSE)
        result = parse_implementation_response(raw)

        assert result["summary"] == "Added rate limiting middleware using slowapi"
        edits = [c for c in result["changes"] if c["action"] == "edit"]
        assert len(edits) == 1
        assert edits[0]["path"] == "src/project_forge/web/app.py"

    def test_parses_create_action(self):
        from project_forge.cron.self_improve_runner import parse_implementation_response

        raw = json.dumps(FAKE_CLAUDE_RESPONSE)
        result = parse_implementation_response(raw)

        creates = [c for c in result["changes"] if c["action"] == "create"]
        assert len(creates) == 1
        assert creates[0]["path"] == "tests/test_rate_limit.py"

    def test_handles_markdown_wrapped_json(self):
        from project_forge.cron.self_improve_runner import parse_implementation_response

        raw = f"```json\n{json.dumps(FAKE_CLAUDE_RESPONSE)}\n```"
        result = parse_implementation_response(raw)

        assert len(result["changes"]) == 2

    def test_raises_on_invalid_json(self):
        from project_forge.cron.self_improve_runner import parse_implementation_response

        with pytest.raises(ValueError, match="parse"):
            parse_implementation_response("not json at all")

    def test_raises_on_missing_changes_key(self):
        from project_forge.cron.self_improve_runner import parse_implementation_response

        with pytest.raises(ValueError, match="changes"):
            parse_implementation_response(json.dumps({"summary": "oops"}))


# ---------------------------------------------------------------------------
# 4. Apply file changes
# ---------------------------------------------------------------------------


class TestApplyChanges:
    """apply_changes writes/edits files on disk relative to project root."""

    def test_apply_create(self, tmp_path):
        from project_forge.cron.self_improve_runner import apply_changes

        changes = [{"path": "tests/newfile.py", "action": "create", "content": "print('hi')\n"}]
        apply_changes(changes, project_root=tmp_path)

        assert (tmp_path / "tests" / "newfile.py").read_text() == "print('hi')\n"

    def test_apply_edit_replaces_text(self, tmp_path):
        from project_forge.cron.self_improve_runner import apply_changes

        target = tmp_path / "tests" / "existing.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("old_line = True\n")
        changes = [
            {
                "path": "tests/existing.py",
                "action": "edit",
                "search": "old_line = True",
                "replace": "new_line = False",
            }
        ]
        apply_changes(changes, project_root=tmp_path)

        assert "new_line = False" in target.read_text()
        assert "old_line" not in target.read_text()

    def test_edit_raises_when_search_not_found(self, tmp_path):
        from project_forge.cron.self_improve_runner import apply_changes

        target = tmp_path / "tests" / "existing.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("nothing here\n")
        changes = [
            {
                "path": "tests/existing.py",
                "action": "edit",
                "search": "nonexistent string",
                "replace": "replacement",
            }
        ]
        with pytest.raises(ValueError, match="not found"):
            apply_changes(changes, project_root=tmp_path)

    def test_create_makes_parent_dirs(self, tmp_path):
        from project_forge.cron.self_improve_runner import apply_changes

        changes = [{"path": "tests/deep/nested/file.py", "action": "create", "content": "x = 1\n"}]
        apply_changes(changes, project_root=tmp_path)

        assert (tmp_path / "tests" / "deep" / "nested" / "file.py").exists()


# ---------------------------------------------------------------------------
# 5. Validate changes (tests + lint)
# ---------------------------------------------------------------------------


class TestValidateChanges:
    """validate_changes runs pytest and ruff, returns pass/fail + output."""

    def test_returns_success_when_both_pass(self):
        from project_forge.cron.self_improve_runner import validate_changes

        with patch("project_forge.cron.self_improve_runner._run_cmd") as mock_run:
            mock_run.return_value = (0, "all passed")
            result = validate_changes()

        assert result["passed"] is True

    def test_returns_failure_when_tests_fail(self):
        from project_forge.cron.self_improve_runner import validate_changes

        def side_effect(cmd, **kwargs):
            if "pytest" in cmd:
                return (1, "FAILED test_foo.py")
            return (0, "ok")

        with patch("project_forge.cron.self_improve_runner._run_cmd", side_effect=side_effect):
            result = validate_changes()

        assert result["passed"] is False
        assert "FAILED" in result["detail"]

    def test_returns_failure_when_lint_fails(self):
        from project_forge.cron.self_improve_runner import validate_changes

        def side_effect(cmd, **kwargs):
            if "ruff" in cmd:
                return (1, "E501 line too long")
            return (0, "ok")

        with patch("project_forge.cron.self_improve_runner._run_cmd", side_effect=side_effect):
            result = validate_changes()

        assert result["passed"] is False


# ---------------------------------------------------------------------------
# 6. Create branch + PR
# ---------------------------------------------------------------------------


def _fake_run_cmd_factory(calls, branch="self-improve-42", fail_on=None):
    """Build a _run_cmd stub that records git calls and simulates success.

    fail_on: substring — any command containing it returns rc 1.
    """

    def fake_run_cmd(cmd, cwd=None):
        calls.append(cmd)
        joined = " ".join(cmd)
        if fail_on and fail_on in joined:
            return 1, f"simulated failure: {joined}"
        if cmd[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            return 0, branch
        return 0, ""

    return fake_run_cmd


class TestCreatePr:
    """create_improvement_pr creates a branch, commits, and opens a PR.

    _run_cmd MUST be mocked here — these tests previously ran real git
    against the dev checkout and committed unrelated dirty files onto
    main under the SI message (#87).
    """

    def test_creates_pr_and_returns_url(self):
        from project_forge.cron.self_improve_runner import create_improvement_pr

        calls = []
        with (
            patch(
                "project_forge.cron.self_improve_runner._run_cmd",
                side_effect=_fake_run_cmd_factory(calls),
            ),
            patch("project_forge.cron.self_improve_runner._run_gh") as mock_gh,
        ):
            mock_gh.return_value = "https://github.com/rayketcham-lab/project-forge/pull/7"
            url = create_improvement_pr(
                issue_number=42,
                summary="Add rate limiting",
                changed_files=["src/project_forge/web/app.py"],
            )

        assert "pull/7" in url

    def test_branch_name_includes_issue_number(self):
        from project_forge.cron.self_improve_runner import create_improvement_pr

        calls = []
        with (
            patch(
                "project_forge.cron.self_improve_runner._run_cmd",
                side_effect=_fake_run_cmd_factory(calls),
            ),
            patch(
                "project_forge.cron.self_improve_runner._run_gh",
                return_value="https://github.com/rayketcham-lab/project-forge/pull/7",
            ),
        ):
            create_improvement_pr(
                issue_number=42,
                summary="Add rate limiting",
                changed_files=["app.py"],
            )

        all_args = " ".join(" ".join(c) for c in calls)
        assert "self-improve-42" in all_args

    def test_reuses_stale_branch_with_checkout_dash_capital_b(self):
        """A leftover branch from a previous cycle must not break branching (#87)."""
        from project_forge.cron.self_improve_runner import create_improvement_pr

        calls = []
        with (
            patch(
                "project_forge.cron.self_improve_runner._run_cmd",
                side_effect=_fake_run_cmd_factory(calls),
            ),
            patch(
                "project_forge.cron.self_improve_runner._run_gh",
                return_value="https://github.com/rayketcham-lab/project-forge/pull/7",
            ),
        ):
            create_improvement_pr(issue_number=42, summary="s", changed_files=["app.py"])

        checkout_calls = [c for c in calls if c[:2] == ["git", "checkout"]]
        assert ["git", "checkout", "-B", "self-improve-42"] in checkout_calls

    def test_aborts_before_commit_when_branch_creation_fails(self):
        """A failed checkout must raise — never stage or commit on main (#87)."""
        from project_forge.cron.self_improve_runner import create_improvement_pr

        calls = []
        with (
            patch(
                "project_forge.cron.self_improve_runner._run_cmd",
                side_effect=_fake_run_cmd_factory(calls, fail_on="checkout -B"),
            ),
            patch("project_forge.cron.self_improve_runner._run_gh"),
        ):
            with pytest.raises(RuntimeError):
                create_improvement_pr(issue_number=42, summary="s", changed_files=["app.py"])

        commit_calls = [c for c in calls if c[:2] == ["git", "commit"]]
        add_calls = [c for c in calls if c[:2] == ["git", "add"]]
        assert commit_calls == [], "must not commit after a failed checkout"
        assert add_calls == [], "must not stage after a failed checkout"

    def test_aborts_when_still_on_main_after_checkout(self):
        """Belt-and-braces: verify HEAD is the SI branch before committing (#87)."""
        from project_forge.cron.self_improve_runner import create_improvement_pr

        calls = []
        with (
            patch(
                "project_forge.cron.self_improve_runner._run_cmd",
                side_effect=_fake_run_cmd_factory(calls, branch="main"),
            ),
            patch("project_forge.cron.self_improve_runner._run_gh"),
        ):
            with pytest.raises(RuntimeError):
                create_improvement_pr(issue_number=42, summary="s", changed_files=["app.py"])

        commit_calls = [c for c in calls if c[:2] == ["git", "commit"]]
        assert commit_calls == [], "must not commit while on main"


class TestCreatePrRealRepoIsolation:
    """Adversarial end-to-end check in a throwaway git repo (#87).

    Seeds an unrelated dirty file, then verifies the SI commit contains
    ONLY the SI-changed file, main is untouched, and the operator's
    dirty file stays dirty and uncommitted.
    """

    @pytest.fixture
    def tmp_repo(self, tmp_path):
        import subprocess

        def git(*args, cwd):
            subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, timeout=30)

        origin = tmp_path / "origin.git"
        origin.mkdir()
        git("init", "--bare", cwd=origin)

        repo = tmp_path / "repo"
        repo.mkdir()
        git("init", "-b", "main", cwd=repo)
        git("config", "user.email", "test@test", cwd=repo)
        git("config", "user.name", "Test", cwd=repo)
        git("remote", "add", "origin", str(origin), cwd=repo)
        (repo / "operator_file.txt").write_text("original\n")
        (repo / "si_target.txt").write_text("original\n")
        git("add", "-A", cwd=repo)
        git("commit", "-m", "init", cwd=repo)
        git("push", "-u", "origin", "main", cwd=repo)
        return repo

    def test_commit_contains_only_si_files(self, tmp_repo, monkeypatch):
        import subprocess

        from project_forge.cron import self_improve_runner as sir

        monkeypatch.setattr(sir, "_PROJECT_ROOT", tmp_repo)
        monkeypatch.setattr(sir, "_run_gh", lambda args: "https://example.com/pull/1")

        # Operator has uncommitted work; SI edits its own target file
        (tmp_repo / "operator_file.txt").write_text("operator WIP — must not be committed\n")
        (tmp_repo / "si_target.txt").write_text("si change\n")

        url = sir.create_improvement_pr(issue_number=7, summary="s", changed_files=["si_target.txt"])
        assert "pull/1" in url

        def git_out(*args):
            return subprocess.run(
                ["git", *args], cwd=tmp_repo, check=True, capture_output=True, text=True, timeout=30
            ).stdout

        # Back on main afterwards, and main gained no commits
        assert git_out("rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
        assert git_out("log", "--oneline", "main").count("\n") == 1

        # The SI branch commit contains exactly the SI file
        committed = git_out("show", "--name-only", "--format=", "self-improve-7").split()
        assert committed == ["si_target.txt"]

        # Operator's dirty file is still dirty, still uncommitted
        assert "operator WIP" in (tmp_repo / "operator_file.txt").read_text()
        assert "operator_file.txt" in git_out("status", "--porcelain")


# ---------------------------------------------------------------------------
# 7. Close issue
# ---------------------------------------------------------------------------


class TestCloseIssue:
    """close_issue closes the GitHub issue after successful PR."""

    def test_closes_issue_by_number(self):
        from project_forge.cron.self_improve_runner import close_issue

        with patch("project_forge.cron.self_improve_runner._run_gh") as mock_gh:
            mock_gh.return_value = ""
            close_issue(42)

        mock_gh.assert_called_once()
        args = mock_gh.call_args[0][0]
        assert "42" in str(args)
        assert "close" in args


# ---------------------------------------------------------------------------
# 8. Full orchestration
# ---------------------------------------------------------------------------


class TestRunSelfImproveCycle:
    """run_self_improve_cycle orchestrates the full autonomous loop."""

    @pytest.mark.asyncio
    async def test_skips_when_no_issues(self):
        from project_forge.cron.self_improve_runner import run_self_improve_cycle

        with patch("project_forge.cron.self_improve_runner.fetch_ci_queue_issues", return_value=[]):
            result = await run_self_improve_cycle()

        assert result["processed"] == 0

    @pytest.mark.asyncio
    async def test_processes_one_issue_end_to_end(self):
        from project_forge.cron.self_improve_runner import run_self_improve_cycle

        with (
            patch(
                "project_forge.cron.self_improve_runner.fetch_ci_queue_issues",
                return_value=[FAKE_ISSUE],
            ),
            patch(
                "project_forge.cron.self_improve_runner.gather_self_context",
                return_value={"code_stats": {}, "test_count": 10},
            ),
            patch("project_forge.cron.self_improve_runner._call_claude") as mock_claude,
            patch("project_forge.cron.self_improve_runner._git_dirty_paths", return_value=set()),
            patch("project_forge.cron.self_improve_runner.apply_changes"),
            patch(
                "project_forge.cron.self_improve_runner.validate_changes",
                return_value={"passed": True, "detail": ""},
            ),
            patch(
                "project_forge.cron.self_improve_runner.create_improvement_pr",
                return_value="https://github.com/rayketcham-lab/project-forge/pull/7",
            ),
            patch("project_forge.cron.self_improve_runner.close_issue"),
            patch("project_forge.cron.self_improve_runner.settings") as mock_settings,
        ):
            mock_settings.anthropic_api_key = "fake-key"
            mock_settings.anthropic_model = "claude-sonnet-4-20250514"
            mock_claude.return_value = json.dumps(FAKE_CLAUDE_RESPONSE)
            result = await run_self_improve_cycle()

        assert result["processed"] == 1
        assert result["results"][0]["status"] == "success"

    @pytest.mark.asyncio
    async def test_skips_issue_when_validation_fails(self):
        from project_forge.cron.self_improve_runner import run_self_improve_cycle

        with (
            patch(
                "project_forge.cron.self_improve_runner.fetch_ci_queue_issues",
                return_value=[FAKE_ISSUE],
            ),
            patch(
                "project_forge.cron.self_improve_runner.gather_self_context",
                return_value={"code_stats": {}, "test_count": 10},
            ),
            patch("project_forge.cron.self_improve_runner._call_claude") as mock_claude,
            patch("project_forge.cron.self_improve_runner._git_dirty_paths", return_value=set()),
            patch("project_forge.cron.self_improve_runner.apply_changes"),
            patch(
                "project_forge.cron.self_improve_runner.validate_changes",
                return_value={"passed": False, "detail": "tests failed"},
            ),
            patch("project_forge.cron.self_improve_runner._revert_changes"),
            patch("project_forge.cron.self_improve_runner.close_issue") as mock_close,
            patch("project_forge.cron.self_improve_runner.settings") as mock_settings,
        ):
            mock_settings.anthropic_api_key = "fake-key"
            mock_settings.anthropic_model = "claude-sonnet-4-20250514"
            mock_claude.return_value = json.dumps(FAKE_CLAUDE_RESPONSE)
            result = await run_self_improve_cycle()

        # Should NOT close the issue when validation fails
        mock_close.assert_not_called()
        assert result["results"][0]["status"] == "validation_failed"

    @pytest.mark.asyncio
    async def test_reverts_on_validation_failure(self):
        from project_forge.cron.self_improve_runner import run_self_improve_cycle

        with (
            patch(
                "project_forge.cron.self_improve_runner.fetch_ci_queue_issues",
                return_value=[FAKE_ISSUE],
            ),
            patch(
                "project_forge.cron.self_improve_runner.gather_self_context",
                return_value={"code_stats": {}, "test_count": 10},
            ),
            patch("project_forge.cron.self_improve_runner._call_claude") as mock_claude,
            patch("project_forge.cron.self_improve_runner._git_dirty_paths", return_value=set()),
            patch("project_forge.cron.self_improve_runner.apply_changes"),
            patch(
                "project_forge.cron.self_improve_runner.validate_changes",
                return_value={"passed": False, "detail": "lint error"},
            ),
            patch("project_forge.cron.self_improve_runner._revert_changes") as mock_revert,
            patch("project_forge.cron.self_improve_runner.close_issue"),
            patch("project_forge.cron.self_improve_runner.settings") as mock_settings,
        ):
            mock_settings.anthropic_api_key = "fake-key"
            mock_settings.anthropic_model = "claude-sonnet-4-20250514"
            mock_claude.return_value = json.dumps(FAKE_CLAUDE_RESPONSE)
            await run_self_improve_cycle()

        mock_revert.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_issue_when_target_files_already_dirty(self):
        """The cycle must not touch files the operator has uncommitted work in (#87)."""
        from project_forge.cron.self_improve_runner import run_self_improve_cycle

        with (
            patch(
                "project_forge.cron.self_improve_runner.fetch_ci_queue_issues",
                return_value=[FAKE_ISSUE],
            ),
            patch(
                "project_forge.cron.self_improve_runner.gather_self_context",
                return_value={"code_stats": {}, "test_count": 10},
            ),
            patch("project_forge.cron.self_improve_runner._call_claude") as mock_claude,
            patch(
                "project_forge.cron.self_improve_runner._git_dirty_paths",
                return_value={"src/project_forge/web/app.py"},
            ),
            patch("project_forge.cron.self_improve_runner.apply_changes") as mock_apply,
            patch("project_forge.cron.self_improve_runner.create_improvement_pr") as mock_pr,
            patch("project_forge.cron.self_improve_runner.close_issue") as mock_close,
            patch("project_forge.cron.self_improve_runner.settings") as mock_settings,
        ):
            mock_settings.anthropic_api_key = "fake-key"
            mock_settings.anthropic_model = "claude-sonnet-4-20250514"
            mock_claude.return_value = json.dumps(FAKE_CLAUDE_RESPONSE)
            result = await run_self_improve_cycle()

        mock_apply.assert_not_called()
        mock_pr.assert_not_called()
        mock_close.assert_not_called()
        assert result["results"][0]["status"] == "dirty_tree"

    @pytest.mark.asyncio
    async def test_proceeds_when_dirty_files_do_not_overlap_targets(self):
        """Unrelated dirty files must not block the cycle — only overlapping ones (#87)."""
        from project_forge.cron.self_improve_runner import run_self_improve_cycle

        with (
            patch(
                "project_forge.cron.self_improve_runner.fetch_ci_queue_issues",
                return_value=[FAKE_ISSUE],
            ),
            patch(
                "project_forge.cron.self_improve_runner.gather_self_context",
                return_value={"code_stats": {}, "test_count": 10},
            ),
            patch("project_forge.cron.self_improve_runner._call_claude") as mock_claude,
            patch(
                "project_forge.cron.self_improve_runner._git_dirty_paths",
                return_value={"docs/unrelated.md"},
            ),
            patch("project_forge.cron.self_improve_runner.apply_changes") as mock_apply,
            patch(
                "project_forge.cron.self_improve_runner.validate_changes",
                return_value={"passed": True, "detail": ""},
            ),
            patch(
                "project_forge.cron.self_improve_runner.create_improvement_pr",
                return_value="https://example.com/pull/9",
            ),
            patch("project_forge.cron.self_improve_runner.close_issue"),
            patch("project_forge.cron.self_improve_runner.settings") as mock_settings,
        ):
            mock_settings.anthropic_api_key = "fake-key"
            mock_settings.anthropic_model = "claude-sonnet-4-20250514"
            mock_claude.return_value = json.dumps(FAKE_CLAUDE_RESPONSE)
            result = await run_self_improve_cycle()

        mock_apply.assert_called_once()
        assert result["results"][0]["status"] == "success"
