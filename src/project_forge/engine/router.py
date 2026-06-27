"""Portfolio-aware idea router.

Classifies generated ideas against the existing GitHub org portfolio and routes
them to one of three outcomes:
- new_project: genuinely novel, save as-is
- contribute: enhancement for an existing repo
- discard: already covered or too derivative
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import anthropic

if TYPE_CHECKING:
    from project_forge.models import Idea, RepoEntry

logger = logging.getLogger(__name__)

RouteAction = Literal["contribute", "new_project", "discard"]

ROUTER_SYSTEM = """You are a portfolio intelligence system for a senior PKI/security software engineer's GitHub org.
Classify new project ideas against the existing portfolio.
Be ruthless: discard anything already covered or too generic/derivative.
Only mark "new_project" for genuinely novel ideas with no existing home."""

_CONTRIBUTE_DESC = (
    '"contribute": Genuine new enhancement/feature for a specific existing repo'
    " (feature doesn't exist yet, but belongs there)"
)
_DISCARD_DESC = (
    '"discard": Already covered by an existing repo\'s scope, too generic, or derivative — not worth tracking'
)
_NEW_PROJECT_DESC = '"new_project": Genuinely novel — no existing repo covers this territory'
_JSON_FORMAT = (
    '{"action": "contribute"|"new_project"|"discard",'
    ' "target_repo": "owner/repo or null",'
    ' "reason": "one clear sentence", "confidence": 0.0-1.0}'
)

ROUTER_PROMPT = (
    "Classify this new idea against the existing repo portfolio.\n\n"
    "IDEA:\n"
    "Name: {name}\n"
    "Tagline: {tagline}\n"
    "Description: {description}\n"
    "Category: {category}\n"
    "MVP Scope: {mvp_scope}\n\n"
    "EXISTING REPOS:\n"
    "{repos}\n\n"
    "CLASSIFY using exactly one action:\n"
    f"- {_CONTRIBUTE_DESC}\n"
    f"- {_DISCARD_DESC}\n"
    f"- {_NEW_PROJECT_DESC}\n\n"
    "Respond ONLY with valid JSON (no markdown):\n"
    f"{{{_JSON_FORMAT}}}"
)

_VALID_ACTIONS: frozenset[str] = frozenset({"contribute", "new_project", "discard"})


@dataclass
class RouteDecision:
    """Result of routing an idea against the portfolio."""

    action: RouteAction
    target_repo: str | None
    reason: str
    confidence: float


class PortfolioRouter:
    """Routes ideas against the current repo registry using Claude."""

    def __init__(self, client: anthropic.Anthropic, model: str) -> None:
        self.client = client
        self.model = model

    def route(self, idea: Idea, repos: list[RepoEntry]) -> RouteDecision:
        """Classify idea against the portfolio and return a routing decision.

        If repos is empty the registry hasn't been seeded yet — we conservatively
        return new_project without calling the API.
        """
        if not repos:
            return RouteDecision(
                action="new_project",
                target_repo=None,
                reason="Registry empty — cannot classify against portfolio",
                confidence=0.5,
            )

        repo_lines = "\n".join(f"- {r.repo_full_name}: {r.description}" for r in repos)
        prompt = ROUTER_PROMPT.format(
            name=idea.name,
            tagline=idea.tagline,
            description=idea.description,
            category=idea.category.value,
            mvp_scope=idea.mvp_scope,
            repos=repo_lines,
        )

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=400,
                system=ROUTER_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text
            return self._parse_decision(text)
        except Exception as exc:
            logger.error("Router API call failed: %s", exc)
            return RouteDecision(
                action="new_project",
                target_repo=None,
                reason=f"Router error: {exc}",
                confidence=0.0,
            )

    @staticmethod
    def _parse_decision(text: str) -> RouteDecision:
        """Parse a JSON routing decision from the model response.

        Strips optional ```json fences. Returns a safe default on any parse failure.
        """
        # Strip markdown code fences
        stripped = text.strip()
        if stripped.startswith("```"):
            # Remove opening fence (```json or ```)
            stripped = stripped.split("\n", 1)[1] if "\n" in stripped else stripped[3:]
            # Remove closing fence
            if "```" in stripped:
                stripped = stripped[: stripped.rfind("```")]
        stripped = stripped.strip()

        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse router JSON: %s — raw: %r", exc, text[:200])
            return RouteDecision(
                action="new_project",
                target_repo=None,
                reason="Parse error — defaulting to new_project",
                confidence=0.0,
            )

        action = data.get("action", "new_project")
        if action not in _VALID_ACTIONS:
            logger.warning("Router returned unknown action %r — defaulting to new_project", action)
            action = "new_project"

        target_repo = data.get("target_repo") or None
        reason = data.get("reason", "No reason provided")
        try:
            confidence = float(data.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5

        return RouteDecision(
            action=action,  # type: ignore[arg-type]
            target_repo=target_repo,
            reason=reason,
            confidence=confidence,
        )
