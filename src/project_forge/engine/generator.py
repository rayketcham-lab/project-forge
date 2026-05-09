"""Idea generation via Claude API."""

import json
import logging

import anthropic

from project_forge.config import settings
from project_forge.engine.prompts import SYSTEM_PROMPT, build_generation_prompt, build_url_ingest_prompt
from project_forge.engine.url_ingest import UrlContent
from project_forge.models import Idea, IdeaCategory

logger = logging.getLogger(__name__)


class IdeaGenerator:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        key = api_key or settings.anthropic_api_key
        if not key:
            import os

            key = os.environ.get("ANTHROPIC_API_KEY", "")
        self.client = anthropic.Anthropic(api_key=key)
        self.model = model or settings.anthropic_model

    async def generate(
        self,
        category: IdeaCategory,
        recent_ideas: list[str] | None = None,
        use_contrarian: bool = False,
        use_combinatoric: bool = False,
        prompt_override: str | None = None,
        portfolio_context: str | None = None,
        *,
        filter_summary: dict | None = None,
        external_seeds: list[dict] | None = None,
    ) -> Idea:
        if prompt_override is not None:
            prompt = prompt_override
        else:
            prompt = build_generation_prompt(
                category=category,
                recent_ideas=recent_ideas or [],
                use_contrarian=use_contrarian,
                use_combinatoric=use_combinatoric,
                portfolio_context=portfolio_context,
                filter_summary=filter_summary,
                external_seeds=external_seeds,
            )

        logger.info("Generating idea for category: %s", category.value)

        response = self.client.messages.create(
            model=self.model,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        idea = self._parse_response(response)
        logger.info("Generated idea: %s (score: %.2f)", idea.name, idea.feasibility_score)
        return idea

    async def generate_from_content(
        self,
        content: UrlContent,
        category_hint: str | None = None,
    ) -> Idea:
        """Generate an idea from URL content."""
        prompt = build_url_ingest_prompt(
            title=content.title,
            url=content.url,
            domain=content.domain,
            content=content.text,
            category_hint=category_hint,
        )

        logger.info("Generating idea from URL: %s", content.url)

        response = self.client.messages.create(
            model=self.model,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        idea = self._parse_response(response, source_url=content.url)
        logger.info("Generated idea from URL: %s (score: %.2f)", idea.name, idea.feasibility_score)
        return idea

    @staticmethod
    def _parse_response_text(text: str, source_url: str | None = None) -> Idea:
        """Parse JSON text from an LLM response into an Idea (shared with adapter)."""
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]

        try:
            data = json.loads(text.strip())
        except json.JSONDecodeError as exc:
            raise ValueError(f"Failed to parse JSON from LLM response: {exc}") from exc

        try:
            kwargs: dict = {
                "name": data["name"],
                "tagline": data["tagline"],
                "description": data["description"],
                "category": IdeaCategory(data["category"]),
                "market_analysis": data["market_analysis"],
                "feasibility_score": max(0.0, min(1.0, float(data["feasibility_score"]))),
                "mvp_scope": data["mvp_scope"],
                "tech_stack": data.get("tech_stack", []),
            }
        except KeyError as exc:
            raise ValueError(f"LLM response missing required field: {exc}") from exc

        if source_url:
            kwargs["source_url"] = source_url

        return Idea(**kwargs)

    @staticmethod
    def _parse_response(response, source_url: str | None = None) -> Idea:
        """Extract and parse JSON from an Anthropic API response into an Idea."""
        text = response.content[0].text
        return IdeaGenerator._parse_response_text(text, source_url=source_url)


class LLMBackendIdeaGenerator:
    """Adapter that lets introspect_runner use any LLMBackend (including
    Claude Code CLI) where it expected an IdeaGenerator. Mirrors the
    minimal IdeaGenerator surface used by run_introspect_cycle:
    `await .generate(category=..., prompt_override=...)`.
    """

    def __init__(self, backend, system_prompt: str | None = None):
        from project_forge.engine.prompts import SYSTEM_PROMPT

        self.backend = backend
        # Concatenate system + user since the Claude Code CLI takes one prompt.
        self.system_prompt = system_prompt or SYSTEM_PROMPT
        # Compatibility shim — IdeaGenerator exposes .client + .model.
        self.client = None
        self.model = backend.name

    async def generate(
        self,
        category: IdeaCategory,
        recent_ideas: list[str] | None = None,  # noqa: ARG002
        use_contrarian: bool = False,  # noqa: ARG002
        use_combinatoric: bool = False,  # noqa: ARG002
        prompt_override: str | None = None,
        portfolio_context: str | None = None,  # noqa: ARG002
        *,
        filter_summary: dict | None = None,  # noqa: ARG002
        external_seeds: list[dict] | None = None,  # noqa: ARG002
    ) -> Idea:
        if prompt_override is None:
            raise ValueError(
                "LLMBackendIdeaGenerator requires prompt_override; "
                "build the prompt in the caller (e.g. introspect path)",
            )
        full_prompt = f"{self.system_prompt}\n\n{prompt_override}"
        text = self.backend.call(full_prompt)
        if not text:
            raise ValueError(f"LLM backend {self.backend.name} returned empty response")
        idea = IdeaGenerator._parse_response_text(text)
        # Force category in case the LLM picked something else
        idea.category = category
        return idea
