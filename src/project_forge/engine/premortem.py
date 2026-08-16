"""Kill Board — adversarial survival analysis (pre-mortem).

Ranks ideas by how likely they are to DIE. The kill board surfaces the
most fragile ideas first so builders can stress-test or kill them early,
rather than discovering the fatal flaw after months of work.

The survival axis (0.0 = almost certain death, 1.0 = strong survivor)
is orthogonal to feasibility (can we build it), fundability (can we sell
it), and ambition (does it push the ceiling). An idea can be technically
feasible and still die on timing, market saturation, or weak
differentiation.

Two-stage like the other scorers: a free deterministic heuristic, then a
cheap LLM tie-break only in the borderline band.

generate_premortem returns a structured pre-mortem dict:
  {
    "case_against": str,          # steel-man argument for failure
    "whos_already_doing_it": [],  # named competitors / existing solutions
    "why_now_wrong": str,         # timing / market readiness problems
    "fatal_risks": [],            # top fatal risks (3-5 items)
    "survival_odds": float,       # 0.0-1.0 (LLM estimate)
  }
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from project_forge.engine.llm_backend import resolve_cheap_backend
from project_forge.models import Idea

logger = logging.getLogger(__name__)

# ── Death-signal patterns ────────────────────────────────────────────────── #

# Buzzword/hype phrases that add noise without signal — high density
# correlates with vague, doomed pitches.
_DEATH_BUZZWORDS = re.compile(
    r"\b(revolutionary|disruptive|game[\s-]?changer|paradigm[\s-]?shift|"
    r"synergy|synergis\w+|world[\s-]?class|next[\s-]?generation|next[\s-]?gen|"
    r"best[\s-]?in[\s-]?class|world['']?s first|first[\s-]?ever|"
    r"nobody has done|unprecedented|groundbreaking|cutting[\s-]?edge|"
    r"state[\s-]?of[\s-]?the[\s-]?art|leverage|impactful|thought[\s-]?leader)\b",
    re.IGNORECASE,
)

# Vague scope language — "build it", "launch it" with no specifics.
_VAGUE_MVP = re.compile(
    r"^\s*(build it|launch it|build and ship|just build|create and launch|"
    r"develop the app|build the platform|create the tool)\s*$",
    re.IGNORECASE,
)

# Grounding signals that RAISE survival odds.
_GROUNDING = re.compile(
    r"(\$\s?\d|\b\d[\d,.]*\s?(k|m|b|million|billion)\b|"
    r"\barr\b|\bmrr\b|\bfund(ed|ing)\b|\braised\b|"
    r"\d[\d,]*\s*(users|customers|downloads|installs|subscribers|stars?)|"
    r"\bwaitlist\b|\bbeta\b|\bpilot\b|\bpaying\b|\brevenue\b)",
    re.IGNORECASE,
)

# Specific differentiation signals — named incumbent, concrete wedge.
_DIFFERENTIATION = re.compile(
    r"\b(vs\.?|versus|alternative to|unlike|better than|instead of|"
    r"replac\w+|compet\w+|open[\s-]?source|self[\s-]?host\w*|"
    r"incumbent|established player|existing tool|wedge)\b",
    re.IGNORECASE,
)

# ── Scoring band ─────────────────────────────────────────────────────────── #

LLM_VERIFY_LOWER = 0.35
LLM_VERIFY_UPPER = 0.65


def score_survival_heuristic(idea: Idea) -> float:
    """Deterministic survival score in [0.0, 1.0].

    Higher = more likely to survive. Penalises buzzword density,
    vague scope, and thin descriptions. Rewards grounded traction
    signals, concrete MVP, and specific differentiation.
    """
    score = 0.30  # baseline — most ideas have some merit

    # Feasibility is the strongest external signal we already have.
    score += idea.feasibility_score * 0.20

    blob = " ".join(
        [
            idea.description or "",
            idea.market_analysis or "",
            idea.mvp_scope or "",
            idea.tagline or "",
        ]
    )

    # Penalise buzzword salad. Cap at 5 hits so one offending sentence
    # doesn't send score negative.
    buzzword_hits = len({m.group(0).lower() for m in _DEATH_BUZZWORDS.finditer(blob)})
    score -= min(buzzword_hits, 5) * 0.04

    # Penalise vague MVP scope ("build it", "just build it").
    if _VAGUE_MVP.match(idea.mvp_scope or ""):
        score -= 0.10

    # Reward grounding signals (traction, dollar figures, user counts).
    grounding_hits = len({m.group(0).lower() for m in _GROUNDING.finditer(blob)})
    score += min(grounding_hits, 4) * 0.04

    # Reward specific differentiation.
    if _DIFFERENTIATION.search(blob):
        score += 0.06

    # Reward named target_incumbent (grounded market demand).
    if (idea.target_incumbent or "").strip():
        score += 0.06

    # Reward description depth. Thin descriptions signal underdeveloped
    # ideas that haven't survived basic scrutiny.
    desc_len = len(idea.description or "")
    if desc_len >= 200:
        score += 0.05
    elif desc_len < 60:
        score -= 0.08

    # Reward a non-empty tech stack (someone thought about implementation).
    if len(idea.tech_stack) >= 2:
        score += 0.04
    elif not idea.tech_stack:
        score -= 0.04

    return max(0.0, min(1.0, score))


async def score_survival(idea: Idea) -> float:
    """Heuristic-first, LLM tie-break in the borderline band."""
    heuristic = score_survival_heuristic(idea)
    if LLM_VERIFY_LOWER <= heuristic <= LLM_VERIFY_UPPER:
        return await _llm_refine(idea, heuristic)
    return heuristic


async def _llm_refine(idea: Idea, heuristic: float) -> float:
    """Ask the cheap LLM for a finer survival estimate."""
    backend = resolve_cheap_backend()
    if backend is None:
        return heuristic
    prompt = (
        "You are a brutally honest startup critic. Rate this project idea's "
        "*survival odds* on a 0.0-1.0 scale. 0.0 = almost certain to die "
        "(bad timing, crowded market, vague differentiation, no real pain). "
        "1.0 = strong survivor (proven pain, clear wedge, right timing). "
        "Be conservative — most ideas die. Respond with JSON only, single "
        "key 'survival_odds'.\n\n"
        f"## Idea: {idea.name}\n"
        f"**Tagline:** {idea.tagline}\n"
        f"**Description:** {idea.description}\n"
        f"**Market:** {idea.market_analysis}\n"
        f"**MVP:** {idea.mvp_scope}\n\n"
        'Reply: {"survival_odds": 0.0-1.0}'
    )
    raw = (await asyncio.to_thread(backend.call, prompt) or "").strip()
    raw = _strip_codefence(raw)
    try:
        data: dict[str, Any] = json.loads(raw)
        s = float(data["survival_odds"])
    except Exception:
        logger.info("premortem LLM refine parse failed; sticking with heuristic")
        return heuristic
    return max(0.0, min(1.0, s))


async def generate_premortem(
    idea: Idea,
    *,
    backend: Any = None,
) -> dict[str, Any]:
    """Run adversarial pre-mortem analysis.

    Returns a dict with keys:
      case_against, whos_already_doing_it, why_now_wrong,
      fatal_risks, survival_odds.

    Falls back to a heuristic-built dict when no backend resolves.
    Injectable backend parameter allows test stubs without network calls.
    """
    resolved = backend if backend is not None else resolve_cheap_backend()
    if resolved is None:
        return _heuristic_fallback(idea)

    prompt = _build_premortem_prompt(idea)
    raw = (await asyncio.to_thread(resolved.call, prompt) or "").strip()
    raw = _strip_codefence(raw)
    try:
        data: dict[str, Any] = json.loads(raw)
        return _normalise_premortem(data, idea)
    except Exception:
        logger.warning(
            "premortem LLM parse failed for idea %s; falling back to heuristic",
            idea.id,
        )
        return _heuristic_fallback(idea)


def _build_premortem_prompt(idea: Idea) -> str:
    return (
        "You are a ruthless startup critic conducting a pre-mortem. Assume "
        "this project has ALREADY FAILED. Work backwards and explain why.\n\n"
        f"## Idea: {idea.name}\n"
        f"**Tagline:** {idea.tagline}\n"
        f"**Description:** {idea.description}\n"
        f"**Market:** {idea.market_analysis}\n"
        f"**MVP scope:** {idea.mvp_scope}\n"
        f"**Tech stack:** {', '.join(idea.tech_stack) or '(unspecified)'}\n\n"
        "Return JSON with these exact keys:\n"
        '  "case_against": "one paragraph steel-man argument for why this fails",\n'
        '  "whos_already_doing_it": ["list", "of", "named", "competitors"],\n'
        '  "why_now_wrong": "one paragraph on timing / market-readiness problems",\n'
        '  "fatal_risks": ["risk 1", "risk 2", "risk 3"],\n'
        '  "survival_odds": 0.0-1.0\n\n'
        "Be brutal and specific. Name real companies when you know them. "
        "Do NOT hedge — commit to the analysis."
    )


def _normalise_premortem(data: dict[str, Any], idea: Idea) -> dict[str, Any]:
    """Validate and normalise the LLM response into the canonical shape."""
    odds_raw = data.get("survival_odds", score_survival_heuristic(idea))
    try:
        odds = max(0.0, min(1.0, float(odds_raw)))
    except (TypeError, ValueError):
        odds = score_survival_heuristic(idea)

    competitors = data.get("whos_already_doing_it", [])
    if not isinstance(competitors, list):
        competitors = [str(competitors)] if competitors else []

    risks = data.get("fatal_risks", [])
    if not isinstance(risks, list):
        risks = [str(risks)] if risks else []

    return {
        "case_against": str(data.get("case_against", "Analysis unavailable.")),
        "whos_already_doing_it": [str(c) for c in competitors[:8]],
        "why_now_wrong": str(data.get("why_now_wrong", "Timing analysis unavailable.")),
        "fatal_risks": [str(r) for r in risks[:5]],
        "survival_odds": odds,
    }


def _heuristic_fallback(idea: Idea) -> dict[str, Any]:
    """Return a minimal pre-mortem when no LLM backend is available."""
    odds = score_survival_heuristic(idea)
    risk_parts: list[str] = []

    if idea.feasibility_score < 0.5:
        risk_parts.append("Low feasibility score — technical risk unresolved.")
    if not (idea.market_analysis or "").strip():
        risk_parts.append("No market analysis — demand is unvalidated.")

    blob = " ".join([idea.description or "", idea.tagline or ""])
    if _DEATH_BUZZWORDS.search(blob):
        risk_parts.append("High buzzword density — substance unclear.")
    if not idea.tech_stack:
        risk_parts.append("No tech stack specified — implementation not thought through.")
    if _VAGUE_MVP.match(idea.mvp_scope or ""):
        risk_parts.append("Vague MVP scope — no clear first deliverable.")

    if not risk_parts:
        risk_parts.append("Insufficient information to identify specific fatal risks.")

    return {
        "case_against": (
            f"'{idea.name}' has not demonstrated clear market demand, "
            "differentiation, or timing advantage. Absent those, execution "
            "risk alone makes survival unlikely."
        ),
        "whos_already_doing_it": [],
        "why_now_wrong": "Timing analysis requires LLM backend — not available.",
        "fatal_risks": risk_parts,
        "survival_odds": odds,
    }


def _strip_codefence(raw: str) -> str:
    """Remove ```json ... ``` or ``` ... ``` wrappers from LLM output."""
    if "```json" in raw:
        return raw.split("```json", 1)[1].split("```", 1)[0].strip()
    if "```" in raw:
        return raw.split("```", 1)[1].split("```", 1)[0].strip()
    return raw
