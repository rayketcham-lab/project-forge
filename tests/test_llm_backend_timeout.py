"""The CLI backend's timeout must be configurable, and generous by default.

Found live: the money-bot panel's revision pass timed out at 180s, which
`stress` reads as "no usable revision" and turns into a FLAGGED verdict.
A strategy was therefore marked as failing review because the reviewer ran
out of clock — the least honest failure mode this engine has.

On a subscription there is no per-call cost, so waiting longer is strictly
better than recording a false verdict.
"""

from __future__ import annotations

import importlib


def _reload(monkeypatch, value: str | None):
    import project_forge.engine.llm_backend as backend

    if value is None:
        monkeypatch.delenv("FORGE_LLM_TIMEOUT_SEC", raising=False)
    else:
        monkeypatch.setenv("FORGE_LLM_TIMEOUT_SEC", value)
    return importlib.reload(backend)


def test_default_timeout_is_generous(monkeypatch):
    backend = _reload(monkeypatch, None)
    assert backend.DEFAULT_TIMEOUT >= 300


def test_timeout_is_env_overridable(monkeypatch):
    backend = _reload(monkeypatch, "900")
    assert backend.DEFAULT_TIMEOUT == 900


def test_garbage_env_falls_back_to_the_default(monkeypatch):
    backend = _reload(monkeypatch, "soon")
    assert backend.DEFAULT_TIMEOUT >= 300
    _reload(monkeypatch, None)


class TestRoleBackends:
    """Generation and review want different models.

    The operator's call, and the right one: drafting a strategy is a
    variety task where a faster model means more shots on goal, while
    reviewing it is where rigor pays. Splitting them lets the panel stay on
    the strongest model without slowing generation to its speed.
    """

    def test_generation_defaults_to_sonnet_on_the_cli(self, monkeypatch):
        import project_forge.engine.llm_backend as backend

        monkeypatch.setattr(backend, "_has_claude_cli", lambda: True)
        monkeypatch.delenv("FORGE_LLM_BACKEND", raising=False)
        monkeypatch.delenv("FORGE_BOT_GEN_MODEL", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("FORGE_ANTHROPIC_API_KEY", raising=False)

        got = backend.resolve_role_backend("generate")
        assert got is not None
        assert "sonnet" in got.name

    def test_review_defaults_to_opus_on_the_cli(self, monkeypatch):
        import project_forge.engine.llm_backend as backend

        monkeypatch.setattr(backend, "_has_claude_cli", lambda: True)
        monkeypatch.delenv("FORGE_LLM_BACKEND", raising=False)
        monkeypatch.delenv("FORGE_BOT_REVIEW_MODEL", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("FORGE_ANTHROPIC_API_KEY", raising=False)

        got = backend.resolve_role_backend("review")
        assert got is not None
        assert "opus" in got.name

    def test_each_role_is_overridable(self, monkeypatch):
        import project_forge.engine.llm_backend as backend

        monkeypatch.setattr(backend, "_has_claude_cli", lambda: True)
        monkeypatch.delenv("FORGE_LLM_BACKEND", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("FORGE_ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("FORGE_BOT_GEN_MODEL", "haiku")
        assert "haiku" in backend.resolve_role_backend("generate").name

    def test_kill_switch_still_wins(self, monkeypatch):
        import project_forge.engine.llm_backend as backend

        monkeypatch.setenv("FORGE_LLM_BACKEND", "none")
        assert backend.resolve_role_backend("generate") is None
