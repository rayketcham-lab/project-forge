"""BYO-LLM backend — generic OpenAI-compatible endpoint.

The operator configures their own model endpoint (local vLLM/Ollama/GGUF or
any chat/completions provider: Grok, OpenAI, Claude-via-proxy). No
vendor-specific SDK. Resolution:

  FORGE_LLM_BACKEND (auto|none|static) override
  → FORGE_LLM_BASE_URL set → OpenAICompatibleBackend
  → None (caller falls back to static heuristics)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from project_forge.config import settings as _settings


# ── OpenAICompatibleBackend (chat/completions over HTTP) ─────────────


class TestOpenAICompatibleBackend:
    def _backend(self, model="qwen-local-m", api_key=""):
        from project_forge.engine.llm_backend import OpenAICompatibleBackend

        return OpenAICompatibleBackend(
            base_url="http://192.0.2.1:8888/v1", model=model, api_key=api_key
        )

    def test_call_returns_content(self):
        with patch("httpx.post") as post:
            post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"choices": [{"message": {"content": '{"name": "Test"}\n'}}]},
            )
            result = self._backend().call("test prompt")
        assert result == '{"name": "Test"}'

    def test_call_posts_to_chat_completions_with_model_and_key(self):
        from project_forge.engine.llm_backend import OpenAICompatibleBackend

        with patch("httpx.post") as post:
            post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"choices": [{"message": {"content": "x"}}]},
            )
            be = OpenAICompatibleBackend(
                base_url="http://192.0.2.1:8888/v1",
                model="qwen-local-m",
                api_key="bearer-token",
            )
            be.call("p")
            args, kwargs = post.call_args
        assert args[0] == "http://192.0.2.1:8888/v1/chat/completions"
        assert kwargs["json"]["model"] == "qwen-local-m"
        assert kwargs["headers"]["Authorization"] == "Bearer bearer-token"

    def test_call_returns_none_on_http_error(self):
        with patch("httpx.post") as post:
            post.return_value = MagicMock(status_code=503, raise_for_status=MagicMock(side_effect=Exception("boom")))
            assert self._backend().call("p") is None

    def test_call_returns_none_on_exception(self):
        with patch("httpx.post", side_effect=RuntimeError("conn refused")):
            assert self._backend().call("p") is None

    def test_call_returns_none_on_empty_content(self):
        with patch("httpx.post") as post:
            post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"choices": [{"message": {"content": "   \n  "}}]},
            )
            assert self._backend().call("p") is None

    def test_name_includes_model(self):
        assert "qwen-local-m" in self._backend(model="qwen-local-m").name


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
        """FORGE_LLM_BACKEND=static/none must disable the cheap path too."""
        from project_forge.engine.llm_backend import resolve_cheap_backend

        monkeypatch.setenv("FORGE_LLM_BACKEND", "static")
        assert resolve_cheap_backend() is None
        monkeypatch.setenv("FORGE_LLM_BACKEND", "none")
        assert resolve_cheap_backend() is None

    def test_returns_none_when_no_base_url(self, monkeypatch):
        from project_forge.engine.llm_backend import resolve_backend

        monkeypatch.delenv("FORGE_LLM_BACKEND", raising=False)
        monkeypatch.delenv("FORGE_LLM_BASE_URL", raising=False)
        monkeypatch.setattr(_settings, "llm_base_url", "")
        assert resolve_backend() is None

    def test_returns_backend_when_base_url_set(self, monkeypatch):
        from project_forge.engine.llm_backend import OpenAICompatibleBackend, resolve_backend

        monkeypatch.delenv("FORGE_LLM_BACKEND", raising=False)
        monkeypatch.setenv("FORGE_LLM_BASE_URL", "http://192.0.2.1:8888/v1")
        monkeypatch.setenv("FORGE_LLM_MODEL", "qwen-local-m")
        monkeypatch.setattr(_settings, "llm_base_url", "http://192.0.2.1:8888/v1")
        be = resolve_backend()
        assert isinstance(be, OpenAICompatibleBackend)

    def test_model_override_via_env(self, monkeypatch):
        from project_forge.engine.llm_backend import resolve_backend

        monkeypatch.delenv("FORGE_LLM_BACKEND", raising=False)
        monkeypatch.setenv("FORGE_LLM_BASE_URL", "http://192.0.2.1:8888/v1")
        monkeypatch.setenv("FORGE_LLM_MODEL", "other-model")
        be = resolve_backend(model_override="cheap-model")
        assert be.model == "cheap-model"
