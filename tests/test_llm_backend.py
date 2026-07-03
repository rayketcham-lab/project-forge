"""TDD: pluggable LLM backend — Anthropic API direct OR Claude Code CLI shell-out.

The user runs Claude Code (Pro Max). We can invoke `claude --print` to
get LLM reasoning without ever provisioning a separate ANTHROPIC_API_KEY
for project-forge — cost rolls into their subscription.

Backend resolution priority:
  FORGE_LLM_BACKEND (api|claude_code|static|none) override
  → ANTHROPIC_API_KEY set → AnthropicAPIBackend
  → `claude` on $PATH → ClaudeCodeBackend
  → None (caller falls back to static heuristics)
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

# ── ClaudeCodeBackend (shells out to `claude --print`) ───────────────


class TestClaudeCodeBackend:
    def test_call_returns_stripped_stdout(self):
        from project_forge.engine.llm_backend import ClaudeCodeBackend

        with patch("subprocess.run") as run:
            run.return_value = MagicMock(stdout='{"name": "Test"}\n', returncode=0)
            be = ClaudeCodeBackend(model="sonnet")
            result = be.call("test prompt")
        assert result == '{"name": "Test"}'

    def test_call_passes_model_flag(self):
        from project_forge.engine.llm_backend import ClaudeCodeBackend

        with patch("subprocess.run") as run:
            run.return_value = MagicMock(stdout="x", returncode=0)
            ClaudeCodeBackend(model="sonnet").call("p")
            args = run.call_args[0][0]
        assert "claude" in args[0]
        assert "--print" in args
        assert "--model" in args
        assert "sonnet" in args
        assert "p" in args

    def test_call_returns_none_on_timeout(self):
        from project_forge.engine.llm_backend import ClaudeCodeBackend

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=1)):
            assert ClaudeCodeBackend().call("p") is None

    def test_call_returns_none_when_claude_missing(self):
        from project_forge.engine.llm_backend import ClaudeCodeBackend

        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert ClaudeCodeBackend().call("p") is None

    def test_call_returns_none_on_nonzero_exit(self):
        from project_forge.engine.llm_backend import ClaudeCodeBackend

        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(returncode=1, cmd=["claude"])):
            assert ClaudeCodeBackend().call("p") is None

    def test_call_returns_none_on_empty_stdout(self):
        from project_forge.engine.llm_backend import ClaudeCodeBackend

        with patch("subprocess.run") as run:
            run.return_value = MagicMock(stdout="   \n  ", returncode=0)
            assert ClaudeCodeBackend().call("p") is None

    def test_name_includes_model(self):
        from project_forge.engine.llm_backend import ClaudeCodeBackend

        assert "sonnet" in ClaudeCodeBackend(model="sonnet").name
        assert "haiku" in ClaudeCodeBackend(model="haiku").name


# ── resolve_backend ──────────────────────────────────────────────────


class TestResolveBackend:
    def test_force_static_returns_none(self, monkeypatch):
        from project_forge.engine.llm_backend import resolve_backend

        monkeypatch.setenv("FORGE_LLM_BACKEND", "static")
        assert resolve_backend() is None

    def test_force_none_returns_none(self, monkeypatch):
        from project_forge.engine.llm_backend import resolve_backend

        monkeypatch.setenv("FORGE_LLM_BACKEND", "none")
        assert resolve_backend() is None

    def test_cheap_backend_honours_static_kill_switch(self, monkeypatch):
        """Regression (found during #84): FORGE_LLM_BACKEND=static/none
        disabled the main generators but resolve_cheap_backend still shelled
        out to the claude CLI for scorers + dedup verification."""
        from project_forge.engine.llm_backend import resolve_cheap_backend

        with patch("shutil.which", return_value="/home/x/bin/claude"):
            monkeypatch.setenv("FORGE_LLM_BACKEND", "static")
            assert resolve_cheap_backend() is None
            monkeypatch.setenv("FORGE_LLM_BACKEND", "none")
            assert resolve_cheap_backend() is None

    def test_force_claude_code_returns_claude_when_available(self, monkeypatch):
        from project_forge.engine.llm_backend import (
            ClaudeCodeBackend,
            resolve_backend,
        )

        monkeypatch.setenv("FORGE_LLM_BACKEND", "claude_code")
        with patch("shutil.which", return_value="/home/x/bin/claude"):
            be = resolve_backend()
        assert isinstance(be, ClaudeCodeBackend)

    def test_force_claude_code_returns_none_when_unavailable(self, monkeypatch):
        from project_forge.engine.llm_backend import resolve_backend

        monkeypatch.setenv("FORGE_LLM_BACKEND", "claude_code")
        with patch(
            "project_forge.engine.llm_backend._has_claude_cli",
            return_value=False,
        ):
            assert resolve_backend() is None

    def test_auto_prefers_api_when_key_set(self, monkeypatch):
        from project_forge.engine.llm_backend import (
            AnthropicAPIBackend,
            resolve_backend,
        )

        monkeypatch.delenv("FORGE_LLM_BACKEND", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        with patch("project_forge.engine.llm_backend._has_claude_cli", return_value=True):
            be = resolve_backend()
        # API beats Claude Code in auto-detect (lower latency)
        assert isinstance(be, AnthropicAPIBackend)

    def test_auto_falls_back_to_claude_code_when_no_api_key(self, monkeypatch):
        from project_forge.engine.llm_backend import (
            ClaudeCodeBackend,
            resolve_backend,
        )

        monkeypatch.delenv("FORGE_LLM_BACKEND", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        # Also clear settings.anthropic_api_key
        from project_forge.config import settings as _settings

        monkeypatch.setattr(_settings, "anthropic_api_key", "")
        with patch("project_forge.engine.llm_backend._has_claude_cli", return_value=True):
            be = resolve_backend()
        assert isinstance(be, ClaudeCodeBackend)

    def test_auto_returns_none_when_nothing_available(self, monkeypatch):
        from project_forge.engine.llm_backend import resolve_backend

        monkeypatch.delenv("FORGE_LLM_BACKEND", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        from project_forge.config import settings as _settings

        monkeypatch.setattr(_settings, "anthropic_api_key", "")
        with patch("project_forge.engine.llm_backend._has_claude_cli", return_value=False):
            assert resolve_backend() is None

    def test_default_model_is_sonnet(self, monkeypatch):
        """User said sonnet for cost. Default must be sonnet, not opus."""
        from project_forge.engine.llm_backend import ClaudeCodeBackend, resolve_backend

        monkeypatch.delenv("FORGE_LLM_BACKEND", raising=False)
        monkeypatch.delenv("FORGE_LLM_MODEL", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        from project_forge.config import settings as _settings

        monkeypatch.setattr(_settings, "anthropic_api_key", "")
        with patch("project_forge.engine.llm_backend._has_claude_cli", return_value=True):
            be = resolve_backend()
        assert isinstance(be, ClaudeCodeBackend)
        assert be.model == "sonnet"

    def test_model_override_via_env(self, monkeypatch):
        from project_forge.engine.llm_backend import resolve_backend

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        from project_forge.config import settings as _settings

        monkeypatch.setattr(_settings, "anthropic_api_key", "")
        monkeypatch.setenv("FORGE_LLM_MODEL", "haiku")
        with patch("project_forge.engine.llm_backend._has_claude_cli", return_value=True):
            be = resolve_backend()
        assert be.model == "haiku"
