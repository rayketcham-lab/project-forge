"""Text ingestion engine — expand a free-form fragment into a full Idea.

Companion to url_ingest.py. The user pastes a thought (could be a half-
formed sentence, a research question, a frustration, a code snippet,
anything text) and the LLM turns it into a structured Idea. Falls back
to a heuristic extractor when no LLM backend is available.
"""

from __future__ import annotations

import logging
import re

from project_forge.engine.llm_backend import resolve_backend
from project_forge.engine.prompts import build_text_ingest_prompt
from project_forge.models import Idea, IdeaCategory

logger = logging.getLogger(__name__)


def _heuristic_idea_from_text(text: str, category_hint: str | None = None) -> Idea:
    """Fallback: build a basic Idea from text without an LLM call.

    Used when no LLM backend is available. Names the idea from the first
    notable phrase, uses the full text as the description, and applies
    the category hint if valid (else defaults to security-tool).
    """
    if category_hint:
        try:
            category = IdeaCategory(category_hint)
        except ValueError:
            category = IdeaCategory.SECURITY_TOOL
    else:
        category = IdeaCategory.SECURITY_TOOL

    # Name: first 4-6 words from the fragment, stripping leading filler
    cleaned = re.sub(r"^\s*(a|an|the|i\s+want|i\s+need|build\s+me)\s+", "", text, flags=re.I)
    name_words = cleaned.split()[:5]
    name = " ".join(name_words).rstrip(".,;:!?").strip() or "User-submitted Idea"
    name = name[:60].title() if not name.isupper() else name[:60]

    # Tagline: first sentence, capped
    sentences = [s.strip() for s in re.split(r"[.!?\n]", text) if len(s.strip()) > 10]
    tagline = sentences[0][:120] if sentences else "User-submitted idea fragment"

    description = f"Idea expanded from a user fragment.\n\nOriginal input:\n{text.strip()}"

    return Idea(
        name=name,
        tagline=tagline,
        description=description,
        category=category,
        market_analysis="Derived from a user fragment — manual market analysis required.",
        feasibility_score=0.6,
        mvp_scope="Review fragment and define scope before implementation.",
        tech_stack=[],
    )


async def generate_idea_from_text(
    text: str,
    category_hint: str | None = None,
) -> Idea:
    """Expand a text fragment into a structured Idea.

    Uses resolve_backend() — Anthropic API direct (when key set) OR
    Claude Code CLI shell-out (when `claude` is on PATH). Falls back to
    a heuristic extractor when neither is available.
    """
    backend = resolve_backend()
    if backend is None:
        logger.info("No LLM backend — heuristic text-ingest fallback")
        return _heuristic_idea_from_text(text=text, category_hint=category_hint)

    logger.info("Text ingest using backend: %s", backend.name)
    prompt = build_text_ingest_prompt(text=text, category_hint=category_hint)
    raw = backend.call(prompt)
    if not raw:
        logger.warning("LLM returned empty for text ingest — falling back to heuristic")
        return _heuristic_idea_from_text(text=text, category_hint=category_hint)

    # Parse the JSON response via the shared IdeaGenerator parser.
    from project_forge.engine.generator import IdeaGenerator

    try:
        idea = IdeaGenerator._parse_response_text(raw)
    except ValueError as exc:
        logger.warning("Could not parse LLM response: %s — falling back to heuristic", exc)
        return _heuristic_idea_from_text(text=text, category_hint=category_hint)

    return idea
