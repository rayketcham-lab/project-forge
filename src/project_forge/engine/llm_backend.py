"""Pluggable LLM backend — Anthropic API direct OR Claude Code CLI shell-out.

The user runs Claude Code (Pro Max). When `claude` is on PATH we can invoke
`claude --print` to get LLM reasoning without ever provisioning a separate
ANTHROPIC_API_KEY for project-forge — cost rolls into their subscription.

Resolution priority:
  FORGE_LLM_BACKEND env (api|claude_code|static|none) — explicit override
  → ANTHROPIC_API_KEY set → AnthropicAPIBackend (lower latency)
  → `claude` on $PATH → ClaudeCodeBackend
  → None (caller falls back to static heuristics)

Default model: sonnet (creative idea generation doesn't need opus, doesn't
benefit from haiku — sonnet is the sweet spot). Override via FORGE_LLM_MODEL.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from typing import Protocol

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "sonnet"
# 180s is generous — short prompts (super-idea cluster naming) finish in
# ~5s, but the introspect prompt is heavy (file tree + commits + lint +
# issues + recent rejections) and Sonnet sometimes takes 60-90s.
DEFAULT_TIMEOUT = 180


class LLMBackend(Protocol):
    """Minimal interface every backend implements."""

    @property
    def name(self) -> str:
        """Human-readable identifier for logs (e.g. 'claude-code:sonnet')."""
        ...

    def call(self, prompt: str) -> str | None:
        """Send prompt, return raw text response (or None on any failure)."""
        ...


class AnthropicAPIBackend:
    """Direct Anthropic API call. Faster than Claude Code (~1-2s vs ~3-8s)
    but requires ANTHROPIC_API_KEY to be set (separate billing tier)."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key)
        # Anthropic API expects full model IDs like 'claude-sonnet-4-6';
        # accept short aliases too for symmetry with the CLI backend.
        self.model = _expand_model_alias(model)

    @property
    def name(self) -> str:
        return f"anthropic-api:{self.model}"

    def call(self, prompt: str) -> str | None:
        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.content[0].text if resp.content else ""
            return text.strip() or None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Anthropic API call failed: %s", exc)
            return None


class ClaudeCodeBackend:
    """Shell out to `claude --print --model <m> <prompt>`.

    Uses host's Claude Code OAuth — no separate API key needed.
    Latency ~3-8s; cost rolls into the user's Claude subscription.
    """

    def __init__(self, model: str = DEFAULT_MODEL, timeout: int = DEFAULT_TIMEOUT):
        self.model = model
        self.timeout = timeout

    @property
    def name(self) -> str:
        return f"claude-code:{self.model}"

    def call(self, prompt: str) -> str | None:
        try:
            result = subprocess.run(
                ["claude", "--print", "--model", self.model, prompt],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=True,
            )
        except FileNotFoundError as exc:
            logger.warning("claude CLI not found on PATH: %s", exc)
            return None
        except subprocess.TimeoutExpired:
            logger.warning("claude --print timed out after %ds", self.timeout)
            return None
        except subprocess.CalledProcessError as exc:
            logger.warning(
                "claude --print exited %d: %s",
                exc.returncode,
                (exc.stderr or "")[:200],
            )
            return None

        out = (result.stdout or "").strip()
        return out or None


def _expand_model_alias(model: str) -> str:
    """Map short aliases (sonnet/opus/haiku) to current full IDs.

    Used for the Anthropic API backend (which wants the full ID); the
    Claude Code CLI accepts the short aliases natively.
    """
    aliases = {
        "sonnet": "claude-sonnet-4-6",
        "opus": "claude-opus-4-7",
        "haiku": "claude-haiku-4-5-20251001",
    }
    return aliases.get(model, model)


def _has_claude_cli() -> bool:
    """True if `claude` is on $PATH. Wrapped so tests can monkeypatch."""
    return shutil.which("claude") is not None


def resolve_backend(*, force: str | None = None) -> LLMBackend | None:
    """Pick the best available LLM backend.

    Returns None when nothing is available — callers must handle this and
    fall back to deterministic heuristics.
    """
    forced = force or os.environ.get("FORGE_LLM_BACKEND")
    model = os.environ.get("FORGE_LLM_MODEL", DEFAULT_MODEL)

    # Settings is a pydantic instance — read the api_key off it (also picks
    # up FORGE_ANTHROPIC_API_KEY via the env_prefix).
    from project_forge.config import settings

    api_key = (
        settings.anthropic_api_key
        or os.environ.get("ANTHROPIC_API_KEY", "")
    )

    if forced in ("static", "none"):
        return None
    if forced == "api":
        return AnthropicAPIBackend(api_key, model=model) if api_key else None
    if forced == "claude_code":
        return ClaudeCodeBackend(model=model) if _has_claude_cli() else None

    # Auto-detect: API beats Claude Code (3-4× lower latency).
    if api_key:
        return AnthropicAPIBackend(api_key, model=model)
    if _has_claude_cli():
        return ClaudeCodeBackend(model=model)
    return None


__all__ = [
    "AnthropicAPIBackend",
    "ClaudeCodeBackend",
    "LLMBackend",
    "resolve_backend",
]
