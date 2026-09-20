"""Pluggable LLM backend — BYO-LLM (bring-your-own OpenAI-compatible endpoint).

The operator supplies their own model endpoint: a LOCAL model (Ollama, vLLM,
LLaMA.cpp GGUF) or any provider that speaks the OpenAI chat/completions
protocol (Grok, OpenAI, or a Claude-compatible proxy). There is no
vendor-specific SDK dependency — configuration is three knobs:

  FORGE_LLM_BASE_URL  e.g. http://192.168.1.130:8888/v1
  FORGE_LLM_MODEL     e.g. Qwen3.8-27B M (UD-Q4_K_M)
  FORGE_LLM_API_KEY   optional bearer key (empty for local/no-auth endpoints)
  FORGE_LLM_BACKEND   explicit override: auto|api|none (defaults to auto)

When no endpoint is configured, backend resolution returns None and callers
fall back to deterministic heuristics — exactly the pre-existing behavior when
no LLM endpoint / API key was available.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Protocol

import httpx

from project_forge.config import settings

logger = logging.getLogger(__name__)

DEFAULT_MODEL = ""


def _timeout_from_env(default_seconds: int = 420) -> int:
    """How long to wait on one LLM call, in seconds.

    Override with FORGE_LLM_TIMEOUT_SEC (or settings.llm_timeout_sec). A
    garbage value warns and falls back rather than crashing at import.
    """
    raw = os.environ.get("FORGE_LLM_TIMEOUT_SEC")
    if raw is None:
        return max(1, int(settings.llm_timeout_sec or default_seconds))
    try:
        value = int(float(raw))
    except ValueError:
        logger.warning("Invalid FORGE_LLM_TIMEOUT_SEC=%r; using %ds", raw, default_seconds)
        return default_seconds
    return value if value > 0 else default_seconds


DEFAULT_TIMEOUT = _timeout_from_env()


class LLMBackend(Protocol):
    """Minimal interface every backend implements."""

    @property
    def name(self) -> str:
        """Human-readable identifier for logs (e.g. 'openai-compatible:Qwen3')."""

    def call(self, prompt: str) -> str | None:
        """Send prompt, return raw text response (or None on any failure)."""


class OpenAICompatibleBackend:
    """Generic chat/completions client against FORGE_LLM_BASE_URL.

    Works with any OpenAI-compatible server — a local vLLM/Ollama/GGUF
    endpoint or a hosted provider. No SDK dependency beyond httpx.
    """

    def __init__(
        self,
        base_url: str,
        model: str = DEFAULT_MODEL,
        api_key: str = "",
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    @property
    def name(self) -> str:
        label = self.model or "default"
        return f"openai-compatible:{label}"

    def call(self, prompt: str) -> str | None:
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 2000,
        }
        try:
            resp = httpx.post(url, headers=headers, json=payload, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return (content or "").strip() or None
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM call to %s failed: %s", url, exc)
            return None


def _configured_base_url() -> str:
    """Base URL from settings/env, or empty when BYO-LLM is disabled."""
    return (settings.llm_base_url or os.environ.get("FORGE_LLM_BASE_URL", "")).strip()


def _configured_model() -> str:
    return (settings.llm_model or os.environ.get("FORGE_LLM_MODEL", "")).strip()


def _configured_api_key() -> str:
    return settings.llm_api_key or os.environ.get("FORGE_LLM_API_KEY", "")


def resolve_backend(
    *,
    force: str | None = None,
    model_override: str | None = None,
) -> LLMBackend | None:
    """Resolve the configured LLM backend, or None when BYO-LLM is disabled.

    `model_override` lets callers pick a different model for cheap, batchy
    work without changing the global default. `FORGE_LLM_BACKEND` can force
    `none` to disable LLM use entirely; `auto`/empty auto-detects from
    FORGE_LLM_BASE_URL.
    """
    forced = force or os.environ.get("FORGE_LLM_BACKEND", "")
    if forced in ("static", "none"):
        return None

    base_url = _configured_base_url()
    if not base_url:
        if forced in ("api", "auto"):
            logger.info("BYO-LLM disabled: no FORGE_LLM_BASE_URL configured")
        return None

    model = model_override or _configured_model()
    return OpenAICompatibleBackend(
        base_url=base_url,
        model=model,
        api_key=_configured_api_key(),
        timeout=DEFAULT_TIMEOUT,
    )


def resolve_cheap_backend() -> LLMBackend | None:
    """Backend for high-volume, batchy work. Same BYO-LLM resolution; model
    falls back to the configured default (there is no separate 'haiku' tier
    in the BYO-LLM model — the operator controls cost at the endpoint)."""
    if os.environ.get("FORGE_LLM_BACKEND", "") in ("static", "none"):
        return None
    return resolve_backend()


# Which model each role gets. With BYO-LLM the operator controls a single
# endpoint/model; role defaults are kept for API-passthrough servers that
# alias short names, otherwise the configured model wins.
_ROLE_DEFAULTS: dict[str, str] = {
    "generate": "FORGE_BOT_GEN_MODEL",
    "review": "FORGE_BOT_REVIEW_MODEL",
}


def resolve_role_backend(role: str) -> LLMBackend | None:
    """Backend for a named role ('generate' / 'review'). Fell back to the
    configured model unless the role env override names a different one."""
    if os.environ.get("FORGE_LLM_BACKEND", "") in ("static", "none"):
        return None
    model_override = os.environ.get(_ROLE_DEFAULTS.get(role, ""), "") or None
    return resolve_backend(model_override=model_override)


__all__ = [
    "OpenAICompatibleBackend",
    "LLMBackend",
    "resolve_backend",
    "resolve_cheap_backend",
    "resolve_role_backend",
]
