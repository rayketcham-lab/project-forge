"""v0.22 self-improve mechanic foundation (#99).

The implement loop (self_improve_runner) already writes code, runs tests +
ruff, commits, and opens a PR — but `_call_claude` used the raw Anthropic
SDK, so on a Pro/Max box with no API key it logged "skipped" and did
nothing. This foundation makes the loop:

  1. run on the SUBSCRIPTION (ClaudeCodeBackend / `claude --print`) when no
     API key is set — the same backend the generation half already uses;
  2. stay DISARMED until FORGE_SELF_IMPROVE_ENABLED is truthy — the
     autonomous cadence no-ops otherwise, so shipping the capability can't
     start an unattended code-modifying loop on the next uvicorn reload;
  3. never edit its own guardrails — the path allow/block-list now blocks
     the runner itself, the (future) mechanic module, and .claude/.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# --------------------------------------------------------------------------- #
# 1. subscription revival                                                     #
# --------------------------------------------------------------------------- #


class TestCallClaudeSubscription:
    def test_uses_api_key_when_present(self):
        import project_forge.cron.self_improve_runner as sir

        msg = MagicMock()
        msg.content = [MagicMock(text='{"ok": 1}')]
        msg.stop_reason = "end_turn"
        client = MagicMock()
        client.messages.create.return_value = msg

        with (
            patch.object(sir, "settings") as mock_settings,
            patch.object(sir.anthropic, "Anthropic", return_value=client) as mk,
        ):
            mock_settings.anthropic_api_key = "sk-real"
            mock_settings.anthropic_model = "claude-sonnet-4-6"
            out = sir._call_claude("hi")

        mk.assert_called_once()
        assert out == '{"ok": 1}'

    def test_falls_back_to_subscription_without_key(self, monkeypatch):
        import project_forge.cron.self_improve_runner as sir

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        backend = MagicMock()
        backend.name = "claude-code:sonnet"
        backend.call = MagicMock(return_value='{"ok": 2}')

        with (
            patch.object(sir, "settings") as mock_settings,
            patch("project_forge.engine.llm_backend.resolve_backend", return_value=backend),
            # The SDK must NOT be touched on the keyless path.
            patch.object(sir.anthropic, "Anthropic", side_effect=AssertionError("SDK used keyless")),
        ):
            mock_settings.anthropic_api_key = ""
            out = sir._call_claude("hi")

        assert out == '{"ok": 2}'
        backend.call.assert_called_once()

    def test_raises_when_no_key_and_no_backend(self, monkeypatch):
        import project_forge.cron.self_improve_runner as sir

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with (
            patch.object(sir, "settings") as mock_settings,
            patch("project_forge.engine.llm_backend.resolve_backend", return_value=None),
        ):
            mock_settings.anthropic_api_key = ""
            with pytest.raises(ValueError):
                sir._call_claude("hi")


# --------------------------------------------------------------------------- #
# 2. kill-switch (disarmed by default)                                        #
# --------------------------------------------------------------------------- #


class TestKillSwitch:
    @pytest.mark.asyncio
    async def test_cadence_noops_when_disabled(self, monkeypatch):
        from project_forge.web import lifespan_scheduler as ls

        monkeypatch.delenv("FORGE_SELF_IMPROVE_ENABLED", raising=False)
        called = {"n": 0}

        async def _fake_cycle():
            called["n"] += 1
            return {"processed": 0, "results": []}

        monkeypatch.setattr(
            "project_forge.cron.self_improve_runner.run_self_improve_cycle",
            _fake_cycle,
        )
        await ls._fire_self_improve(None)
        assert called["n"] == 0  # disarmed → never runs

    @pytest.mark.asyncio
    async def test_cadence_runs_when_armed(self, monkeypatch):
        from project_forge.web import lifespan_scheduler as ls

        monkeypatch.setenv("FORGE_SELF_IMPROVE_ENABLED", "1")
        called = {"n": 0}

        async def _fake_cycle():
            called["n"] += 1
            return {"processed": 0, "results": []}

        monkeypatch.setattr(
            "project_forge.cron.self_improve_runner.run_self_improve_cycle",
            _fake_cycle,
        )
        await ls._fire_self_improve(None)
        assert called["n"] == 1


# --------------------------------------------------------------------------- #
# 3. self-edit guardrail                                                      #
# --------------------------------------------------------------------------- #


class TestSelfEditGuardrail:
    def test_blocks_editing_own_runner(self, tmp_path):
        from project_forge.cron.self_improve_runner import _validate_path

        with pytest.raises(ValueError):
            _validate_path("src/project_forge/cron/self_improve_runner.py", tmp_path)

    def test_blocks_editing_mechanic(self, tmp_path):
        from project_forge.cron.self_improve_runner import _validate_path

        with pytest.raises(ValueError):
            _validate_path("src/project_forge/engine/mechanic.py", tmp_path)

    def test_blocks_claude_settings(self, tmp_path):
        from project_forge.cron.self_improve_runner import _validate_path

        with pytest.raises(ValueError):
            _validate_path(".claude/settings.json", tmp_path)

    def test_blocks_github_workflows(self, tmp_path):
        from project_forge.cron.self_improve_runner import _validate_path

        with pytest.raises(ValueError):
            _validate_path(".github/workflows/ci.yml", tmp_path)

    def test_still_allows_a_normal_engine_file(self, tmp_path):
        from project_forge.cron.self_improve_runner import _validate_path

        out = _validate_path("src/project_forge/engine/fundability.py", tmp_path)
        assert str(out).endswith("fundability.py")
