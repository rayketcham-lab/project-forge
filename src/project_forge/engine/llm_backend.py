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
        # Resolve absolute path each call so a systemd-launched service
        # without ~/.local/bin on PATH still finds the binary.
        bin_path = _claude_cli_path() or "claude"
        try:
            result = subprocess.run(
                [bin_path, "--print", "--model", self.model, prompt],
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


def _claude_cli_path() -> str | None:
    """Locate the `claude` CLI. Tries $PATH first, then common install
    locations (npm-global, ~/.local/bin) since systemd services often
    start with a minimal PATH that excludes user-local bin directories.
    """
    found = shutil.which("claude")
    if found:
        return found
    candidates = [
        os.path.expanduser("~/.local/bin/claude"),
        "/home/claude/.local/bin/claude",
        os.path.expanduser("~/.npm-global/bin/claude"),
        "/usr/local/bin/claude",
    ]
    for p in candidates:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def _has_claude_cli() -> bool:
    """True if `claude` is on $PATH or a common install location.

    Wrapped so tests can monkeypatch.
    """
    return _claude_cli_path() is not None


def resolve_backend(
    *,
    force: str | None = None,
    model_override: str | None = None,
) -> LLMBackend | None:
    """Pick the best available LLM backend.

    `model_override` lets callers pick a different model for cheap, batchy
    work (e.g. Haiku 4.5 for semantic dedup verification) without changing
    the global default Sonnet model. Falls back to `FORGE_LLM_MODEL` env,
    then `DEFAULT_MODEL`.

    Returns None when nothing is available — callers must handle this and
    fall back to deterministic heuristics.
    """
    forced = force or os.environ.get("FORGE_LLM_BACKEND")
    model = model_override or os.environ.get("FORGE_LLM_MODEL", DEFAULT_MODEL)

    # Settings is a pydantic instance — read the api_key off it (also picks
    # up FORGE_ANTHROPIC_API_KEY via the env_prefix).
    from project_forge.config import settings

    # For Haiku-specific work, prefer a dedicated Haiku key so the user can
    # plumb a cheap key without giving project-forge their Sonnet/Opus key.
    if model_override == "haiku":
        api_key = os.environ.get("FORGE_HAIKU_API_KEY", "") or (
            settings.anthropic_api_key
            or os.environ.get("ANTHROPIC_API_KEY", "")
        )
    else:
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


def resolve_cheap_backend() -> LLMBackend | None:
    """Backend for high-volume, batchy work (dedup verification, cluster
    naming, tie-break scoring).

    Model selection differs by path because the cost model differs:

      - API path (FORGE_HAIKU_API_KEY or ANTHROPIC_API_KEY set):
        prefer Haiku 4.5. ~$0.001/call, fast. The cost discipline
        matters because every API call shows up on a bill.

      - Claude Code CLI path (no API key, running on the user's Pro
        Max subscription): the 'cheap' name is misleading — there is
        no per-call cost — so we use the strongest available model.
        Default `FORGE_CLI_MODEL` is `opus`; override via env.

    Falls through to `resolve_backend()` if nothing usable resolves.
    """
    # API path: Haiku is the right call because cost is real per token.
    haiku_via_api = resolve_backend(model_override="haiku")
    if haiku_via_api is not None and isinstance(haiku_via_api, AnthropicAPIBackend):
        return haiku_via_api

    # CLI path: no per-call cost on Pro Max. Use the most capable model
    # the user has access to. Default Opus; FORGE_CLI_MODEL overrides
    # ("sonnet" / "haiku" / etc.) for users who want a different tradeoff.
    forced = os.environ.get("FORGE_LLM_BACKEND", "")
    if forced != "api" and _has_claude_cli():
        cli_model = os.environ.get("FORGE_CLI_MODEL", "opus")
        return ClaudeCodeBackend(model=cli_model)

    return resolve_backend()


__all__ = [
    "AnthropicAPIBackend",
    "ClaudeCodeBackend",
    "LLMBackend",
    "resolve_backend",
    "resolve_cheap_backend",
]
