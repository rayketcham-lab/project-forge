"""Idea generation via a BYO-LLM OpenAI-compatible backend."""

import asyncio
import json
import logging

from project_forge.config import settings
from project_forge.engine.llm_backend import OpenAICompatibleBackend, resolve_backend
from project_forge.engine.prompts import SYSTEM_PROMPT, build_generation_prompt, build_url_ingest_prompt
from project_forge.engine.url_ingest import UrlContent
from project_forge.models import Idea, IdeaCategory

logger = logging.getLogger(__name__)


class IdeaGenerator:
    """LLM idea generator backed by the generic BYO-LLM backend.

    Resolves via `resolve_backend()`; when no endpoint is configured it
    falls back to a directly-constructed OpenAICompatibleBackend so callers
    that pass an explicit api_key/model (cron entry points, tests) still
    construct a usable object.
    """

    def __init__(self, api_key: str | None = None, model: str | None = None, backend=None):
        self.backend = backend
        if self.backend is None:
            self.backend = resolve_backend(model_override=model)
        if self.backend is None:
            self.backend = OpenAICompatibleBackend(
                base_url=settings.llm_base_url,
                model=model or settings.llm_model,
                api_key=api_key or settings.llm_api_key,
            )
        self.model = model or settings.llm_model or getattr(self.backend, "model", "") or ""
        # Compatibility shim — some callers read .client / .model.
        self.client = None

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

        full_prompt = f"{SYSTEM_PROMPT}\n\n{prompt}"
        text = await asyncio.to_thread(self.backend.call, full_prompt)
        if not text:
            raise ValueError(f"LLM backend {self.backend.name} returned empty response")

        idea = self._parse_response_text(text)
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

        full_prompt = f"{SYSTEM_PROMPT}\n\n{prompt}"
        text = await asyncio.to_thread(self.backend.call, full_prompt)
        if not text:
            raise ValueError(f"LLM backend {self.backend.name} returned empty response")

        idea = self._parse_response_text(text, source_url=content.url)
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


class LLMBackendIdeaGenerator:
    """Adapter that lets introspect_runner use any LLMBackend where it
    expected an IdeaGenerator. Mirrors the minimal IdeaGenerator surface
    used by run_introspect_cycle: `await .generate(category=..., prompt_override=...)`.
    """

    def __init__(self, backend, system_prompt: str | None = None):
        from project_forge.engine.prompts import SYSTEM_PROMPT

        self.backend = backend
        # Concatenate system + user since the BYO-LLM backend takes one prompt.
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

    async def generate_from_content(
        self,
        content: UrlContent,
        category_hint: str | None = None,
    ) -> Idea:
        """Generate an idea from URL content via the backend."""
        prompt = build_url_ingest_prompt(
            title=content.title,
            url=content.url,
            domain=content.domain,
            content=content.text,
            category_hint=category_hint,
        )
        full_prompt = f"{self.system_prompt}\n\n{prompt}"
        text = self.backend.call(full_prompt)
        if not text:
            raise ValueError(f"LLM backend {self.backend.name} returned empty response")
        return IdeaGenerator._parse_response_text(text, source_url=content.url)
