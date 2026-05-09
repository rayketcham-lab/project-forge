"""Multi-step idea builder wizard — 5 phases of LLM-driven follow-up questions.

User pastes a fragment → wizard probes deeper through 5 phases, each
asking 2-3 intelligent follow-up questions based on prior answers, then
synthesizes a structured Idea. Backend is stateless: frontend
accumulates state, POSTs the full state on each step.

Phases:
  1. Discover     — clarify core problem (who hits it, when, frequency)
  2. Differentiate — what's missing in existing tools, unique angle
  3. Audience     — concrete persona, buyer, decision-maker
  4. Constraints  — tech stack, must-haves, deployment model
  5. Synthesize   — produce the structured Idea draft
"""

from __future__ import annotations

import json
import logging
import re

from project_forge.engine.llm_backend import resolve_backend
from project_forge.models import IdeaCategory

logger = logging.getLogger(__name__)


PHASE_NAMES = {
    1: "Discover",
    2: "Differentiate",
    3: "Audience",
    4: "Constraints",
    5: "Synthesize",
}

PHASE_GUIDANCE = {
    1: (
        "Probe the CORE PROBLEM. Ask 2-3 sharp questions that surface: who "
        "hits this problem most often, when (what trigger), and how painful "
        "it is today. Don't ask about solutions yet."
    ),
    2: (
        "Probe DIFFERENTIATION. Given what you've learned about the problem, "
        "ask 2-3 questions that surface what existing tools get wrong, what "
        "the user thinks is missing, and where the unique angle lies. Avoid "
        "rehashing problem questions."
    ),
    3: (
        "Probe AUDIENCE. Ask 2-3 questions that pin down the SPECIFIC user "
        "and buyer (concrete role at a concrete organization size) — not "
        "abstract personas. Surface budget owner vs. day-to-day user."
    ),
    4: (
        "Probe CONSTRAINTS. Ask 2-3 questions about technical must-haves, "
        "tech stack preferences, deployment model (SaaS vs. self-host vs. "
        "CLI vs. library), and any non-functional requirements (latency, "
        "compliance, integration touchpoints)."
    ),
    5: (
        "SYNTHESIZE. Using everything the user told you across the prior "
        "four phases, produce a sharp, specific project idea. KEEP THE "
        "USER'S INTENT. Do not drift to a generic dashboard if they asked "
        "for a specific reconciliation tool."
    ),
}


def build_step_prompt(
    *,
    step: int,
    fragment: str,
    answers: list[dict],
    category_hint: str | None = None,
) -> str:
    """Render the LLM prompt for a given wizard step."""
    if step not in (1, 2, 3, 4, 5):
        raise ValueError(f"step must be 1-5, got {step}")

    phase = PHASE_NAMES[step]
    guidance = PHASE_GUIDANCE[step]

    # Render accumulated Q/A so the LLM has the full context.
    if answers:
        qa_lines = "\n".join(
            f"  Q{i+1}: {a.get('question', '?')}\n  A{i+1}: {a.get('answer', '')}"
            for i, a in enumerate(answers)
        )
        qa_block = f"\nAnswers so far:\n{qa_lines}\n"
    else:
        qa_block = "\nNo answers yet — this is step 1.\n"

    if category_hint:
        cat_block = f"User-selected category hint: {category_hint}\n"
    else:
        all_cats = ", ".join(c.value for c in IdeaCategory)
        cat_block = f"Auto-detect category from: {all_cats}\n"

    if step < 5:
        # Steps 1-4: ask follow-up questions.
        response_block = (
            "\nRespond with ONLY valid JSON in this exact shape:\n"
            '{"questions": ["concrete question 1", "concrete question 2", "concrete question 3"]}\n'
            "\n2-3 questions max. Each question should be specific enough that "
            "the answer changes the eventual idea. No yes/no questions."
        )
    else:
        # Step 5: synthesize the full Idea draft.
        response_block = (
            "\nRespond with ONLY valid JSON in this exact shape:\n"
            "{\n"
            '  "draft": {\n'
            '    "name": "Short Project Name (2-4 words)",\n'
            '    "tagline": "One-sentence hook (under 100 chars)",\n'
            '    "description": "2-3 paragraph pitch",\n'
            f'    "category": "{category_hint or "security-tool"}",\n'
            '    "market_analysis": "Why this matters now, what the gap is",\n'
            '    "feasibility_score": 0.75,\n'
            '    "mvp_scope": "Concrete description of the MVP",\n'
            '    "tech_stack": ["python", "fastapi"]\n'
            "  }\n"
            "}\n"
            "\nfeasibility_score 0.0-1.0. Keep the user's intent — be specific, "
            "don't drift to a generic dashboard."
        )

    return (
        f"You are an idea-builder running a 5-phase wizard.\n"
        f"\nCurrent phase: {step}/5 — {phase}\n"
        f"Phase task: {guidance}\n"
        f"\nOriginal fragment from user:\n---\n{fragment}\n---\n"
        f"{cat_block}"
        f"{qa_block}"
        f"{response_block}"
    )


def parse_step_response(raw: str, step: int) -> dict:
    """Parse the LLM's JSON response. Returns {questions: ...} or {draft: ...}
    on success, {error: ...} on parse failure.
    """
    text = (raw or "").strip()
    # Strip markdown fences if present.
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0]
    text = text.strip()

    if not text:
        return {"error": "empty response from LLM"}

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("idea_builder: bad JSON at step %d: %s", step, exc)
        return {"error": f"invalid JSON: {exc}"}

    if step < 5:
        questions = parsed.get("questions")
        if isinstance(questions, list) and questions:
            return {"questions": [str(q) for q in questions[:5]]}
        return {"error": "response missing 'questions' array"}

    draft = parsed.get("draft")
    if isinstance(draft, dict) and draft.get("name"):
        return {"draft": draft}
    return {"error": "response missing 'draft' object"}


def run_wizard_step(
    *,
    step: int,
    fragment: str,
    answers: list[dict],
    category_hint: str | None = None,
) -> dict:
    """Execute one step: build prompt, call LLM, parse response.

    Returns one of:
      {"questions": [...]}      for steps 1-4
      {"draft": {...idea...}}   for step 5
      {"error": "..."}          on any failure
      None                      when no LLM backend available
    """
    backend = resolve_backend()
    if backend is None:
        return None

    prompt = build_step_prompt(
        step=step, fragment=fragment, answers=answers, category_hint=category_hint,
    )
    raw = backend.call(prompt)
    if not raw:
        return {"error": "LLM returned empty response"}
    return parse_step_response(raw, step=step)


# pre-compile a regex used by tests / external callers
_FRAGMENT_MIN_RE = re.compile(r"\S")


def is_meaningful_fragment(text: str) -> bool:
    """True iff fragment has at least one non-whitespace character."""
    return bool(_FRAGMENT_MIN_RE.search(text or ""))
