"""Tests for GitHub integration (mocked gh CLI)."""

from unittest.mock import MagicMock, patch

import pytest

from project_forge.scaffold.github import (
    create_issue,
    create_label,
    create_repo,
    push_initial_commit,
)


class TestGitHub:
    @patch("project_forge.scaffold.github.subprocess.run")
    def test_create_repo(self, mock_run):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "https://github.com/rayketcham/test-repo"
        mock_run.return_value.stderr = ""

        url = create_repo("test-repo", "A test repo")
        assert "test-repo" in url
        mock_run.assert_called_once()

    @patch("project_forge.scaffold.github.subprocess.run")
    def test_create_repo_failure(self, mock_run):
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = "already exists"

        with pytest.raises(RuntimeError, match="already exists"):
            create_repo("test-repo", "A test repo")

    @patch("project_forge.scaffold.github.subprocess.run")
    def test_create_issue(self, mock_run):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "https://github.com/rayketcham/test-repo/issues/1"
        mock_run.return_value.stderr = ""

        url = create_issue("rayketcham/test-repo", "Test issue", "Body text")
        assert "issues/1" in url

    @patch("project_forge.scaffold.github.subprocess.run")
    def test_create_issue_with_labels(self, mock_run):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "https://github.com/rayketcham/test-repo/issues/2"
        mock_run.return_value.stderr = ""

        url = create_issue("rayketcham/test-repo", "Test", "Body", labels=["idea", "feature"])
        assert "issues/2" in url
        call_args = mock_run.call_args[0][0]
        assert "--label" in call_args

    @patch("project_forge.scaffold.github.subprocess.run")
    def test_create_label(self, mock_run):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""

        create_label("rayketcham/test-repo", "idea", "0e8a16", "Generated idea")
        mock_run.assert_called_once()

    @patch("project_forge.scaffold.github.subprocess.run")
    def test_create_label_already_exists(self, mock_run):
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = "already exists"

        # Should not raise, just warn
        create_label("rayketcham/test-repo", "idea", "0e8a16")


class TestPushInitialCommit:
    """Tests for push_initial_commit."""

    _REMOTE_URL = "https://github.com/rayketcham/test-repo"
    _PROJECT_DIR = "/tmp/fake-project"  # noqa: S108

    def _make_ok(self, stdout: str = "") -> MagicMock:
        """Return a successful subprocess.CompletedProcess mock."""
        m = MagicMock()
        m.returncode = 0
        m.stdout = stdout
        m.stderr = ""
        return m

    def _make_fail(self, stderr: str = "error") -> MagicMock:
        """Return a failed subprocess.CompletedProcess mock."""
        m = MagicMock()
        m.returncode = 1
        m.stdout = ""
        m.stderr = stderr
        return m

    @patch("project_forge.scaffold.github.subprocess.run")
    def test_push_initial_commit_runs_git_commands(self, mock_run):
        """All expected git subcommands are called in order."""
        token = "ghs_testtoken"  # noqa: S105
        mock_run.return_value = self._make_ok()
        # gh auth token call returns the token
        mock_run.side_effect = [
            self._make_ok(),  # git init
            self._make_ok(),  # git branch -M main
            self._make_ok(),  # git add -A
            self._make_ok(),  # git commit
            self._make_ok(),  # git remote add
            self._make_ok(token),  # gh auth token
            self._make_ok(),  # git remote set-url (with token)
            self._make_ok(),  # git push
            self._make_ok(),  # git remote set-url (cleanup)
        ]

        push_initial_commit(self._PROJECT_DIR, self._REMOTE_URL)

        called_cmds = [c[0][0] for c in mock_run.call_args_list]

        assert called_cmds[0] == ["git", "init"]
        assert called_cmds[1] == ["git", "branch", "-M", "main"]
        assert called_cmds[2] == ["git", "add", "-A"]
        assert called_cmds[3][0:2] == ["git", "commit"]
        assert called_cmds[4][0:4] == ["git", "remote", "add", "origin"]
        assert called_cmds[5] == ["gh", "auth", "token"]
        # set-url with embedded token
        assert called_cmds[6][0:3] == ["git", "remote", "set-url"]
        assert token in called_cmds[6][-1]
        assert called_cmds[7] == ["git", "push", "-u", "origin", "main"]
        # final cleanup set-url — no token
        assert called_cmds[8][0:3] == ["git", "remote", "set-url"]
        assert token not in called_cmds[8][-1]

    @patch("project_forge.scaffold.github.subprocess.run")
    def test_push_initial_commit_strips_token_after_push(self, mock_run):
        """The final git remote set-url restores the clean URL (no token)."""
        token = "ghs_secrettoken"  # noqa: S105
        mock_run.side_effect = [
            self._make_ok(),  # git init
            self._make_ok(),  # git branch -M main
            self._make_ok(),  # git add -A
            self._make_ok(),  # git commit
            self._make_ok(),  # git remote add
            self._make_ok(token),  # gh auth token
            self._make_ok(),  # git remote set-url (with token)
            self._make_ok(),  # git push
            self._make_ok(),  # git remote set-url (cleanup)
        ]

        push_initial_commit(self._PROJECT_DIR, self._REMOTE_URL)

        last_call_args = mock_run.call_args_list[-1][0][0]
        assert last_call_args[0:3] == ["git", "remote", "set-url"]
        # The restored URL must equal the original (no token embedded)
        assert last_call_args[-1] == self._REMOTE_URL
        assert token not in last_call_args[-1]

    @patch("project_forge.scaffold.github.subprocess.run")
    def test_push_initial_commit_git_init_failure_raises(self, mock_run):
        """A failing git init propagates as RuntimeError."""
        mock_run.side_effect = [
            self._make_fail("not a git repository"),  # git init fails
        ]

        with pytest.raises(RuntimeError, match="Git command failed"):
            push_initial_commit(self._PROJECT_DIR, self._REMOTE_URL)

    @patch("project_forge.scaffold.github.subprocess.run")
    def test_push_initial_commit_push_failure_raises(self, mock_run):
        """A failing git push propagates as RuntimeError."""
        token = "ghs_pushfailtoken"  # noqa: S105
        mock_run.side_effect = [
            self._make_ok(),  # git init
            self._make_ok(),  # git branch -M main
            self._make_ok(),  # git add -A
            self._make_ok(),  # git commit
            self._make_ok(),  # git remote add
            self._make_ok(token),  # gh auth token
            self._make_ok(),  # git remote set-url (with token)
            self._make_fail("remote: Repository not found."),  # git push fails
            self._make_ok(),  # git remote set-url (cleanup — should still run)
        ]

        with pytest.raises(RuntimeError, match="Git push failed"):
            push_initial_commit(self._PROJECT_DIR, self._REMOTE_URL)

    @patch("project_forge.scaffold.github.subprocess.run")
    def test_push_initial_commit_token_cleaned_on_push_failure(self, mock_run):
        """Token is removed from remote URL even when push fails (try/finally).

        NOTE: This test is expected to FAIL against the current implementation
        because push_initial_commit has no try/finally around the push step.
        It documents the bug and drives the GREEN fix.
        """
        token = "ghs_leaktoken"  # noqa: S105
        mock_run.side_effect = [
            self._make_ok(),  # git init
            self._make_ok(),  # git branch -M main
            self._make_ok(),  # git add -A
            self._make_ok(),  # git commit
            self._make_ok(),  # git remote add
            self._make_ok(token),  # gh auth token
            self._make_ok(),  # git remote set-url (with token)
            self._make_fail("network error"),  # git push fails
            self._make_ok(),  # git remote set-url (cleanup)
        ]

        with pytest.raises(RuntimeError, match="Git push failed"):
            push_initial_commit(self._PROJECT_DIR, self._REMOTE_URL)

        # Regardless of push failure, cleanup set-url must have been called
        # with the clean (token-free) URL as the final set-url invocation.
        set_url_calls = [
            c for c in mock_run.call_args_list if c[0][0][0:3] == ["git", "remote", "set-url"]
        ]
        assert len(set_url_calls) == 2, (
            "Expected two git remote set-url calls: one to embed token, one to clean it up"
        )
        cleanup_call_url = set_url_calls[-1][0][0][-1]
        assert token not in cleanup_call_url, (
            f"Token was NOT cleaned from remote URL after push failure — leaked: {cleanup_call_url}"
        )
