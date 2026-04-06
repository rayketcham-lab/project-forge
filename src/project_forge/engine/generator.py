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
    ) -> Idea:
        if prompt_override is not None:
            prompt = prompt_override
        else:
            prompt = build_generation_prompt(
                category=category,
                recent_ideas=recent_ideas or [],
                use_contrarian=use_contrarian,
                use_combinatoric=use_combinatoric,
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
    def _parse_response(response, source_url: str | None = None) -> Idea:
        """Extract and parse JSON from an API response into an Idea."""
        text = response.content[0].text
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]

        try:
            data = json.loads(text.strip())
        except json.JSONDecodeError as exc:
            raise ValueError(f"Failed to parse JSON from API response: {exc}") from exc

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
            raise ValueError(f"API response missing required field: {exc}") from exc

        if source_url:
            kwargs["source_url"] = source_url

        return Idea(**kwargs)
