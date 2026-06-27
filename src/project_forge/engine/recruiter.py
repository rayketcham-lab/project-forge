"""THE RECRUITER — autonomous build-cost estimator.

Turns an Idea into a staffed build plan: roles, headcount, duration,
total person-weeks, a rough cost band, and a complexity rating (1-5).

Two-stage (mirrors snipe.py / ambition.py):

  1. Heuristic (always runs, ~free): derives complexity from tech-stack
     breadth, MVP-scope word count, and feasibility score, then maps
     that to canonical role mixes and time estimates.

  2. LLM refinement (optional): when a backend is provided (or
     auto-resolved via resolve_cheap_backend) the heuristic is sent as
     a baseline and the LLM is asked for a finer estimate. If the
     response is malformed or the LLM is unavailable the heuristic is
     returned unchanged — no silent failure.

Public API:
    estimate_build(idea, *, backend=None) -> dict
    format_estimate_markdown(est) -> str
"""

from __future__ import annotations

import json
import logging
from typing import Any

from project_forge.engine.llm_backend import resolve_cheap_backend
from project_forge.models import Idea

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Role mix templates indexed by complexity (1-5)                              #
# --------------------------------------------------------------------------- #

_ROLE_TEMPLATES: dict[int, list[dict[str, Any]]] = {
    1: [
        {"role": "Backend Engineer", "count": 1, "weeks": 4},
    ],
    2: [
        {"role": "Backend Engineer", "count": 1, "weeks": 8},
        {"role": "Frontend Engineer", "count": 1, "weeks": 6},
    ],
    3: [
        {"role": "Backend Engineer", "count": 1, "weeks": 12},
        {"role": "Frontend Engineer", "count": 1, "weeks": 10},
        {"role": "DevOps Engineer", "count": 1, "weeks": 6},
    ],
    4: [
        {"role": "Backend Engineer", "count": 2, "weeks": 20},
        {"role": "Frontend Engineer", "count": 1, "weeks": 16},
        {"role": "DevOps Engineer", "count": 1, "weeks": 10},
        {"role": "Product Manager", "count": 1, "weeks": 20},
    ],
    5: [
        {"role": "Backend Engineer", "count": 2, "weeks": 32},
        {"role": "Frontend Engineer", "count": 2, "weeks": 28},
        {"role": "DevOps Engineer", "count": 1, "weeks": 16},
        {"role": "Product Manager", "count": 1, "weeks": 32},
        {"role": "Security Engineer", "count": 1, "weeks": 12},
    ],
}

# Skills implied by each complexity tier, independent of the idea's own stack.
_TIER_SKILLS: dict[int, list[str]] = {
    1: ["REST API", "SQL", "testing"],
    2: ["REST API", "SQL", "React/Vue", "testing"],
    3: ["REST API", "SQL", "React/Vue", "CI/CD", "Docker"],
    4: ["REST API", "SQL", "React/Vue", "CI/CD", "Docker", "product-roadmap", "analytics"],
    5: [
        "REST API",
        "SQL",
        "React/Vue",
        "CI/CD",
        "Docker",
        "product-roadmap",
        "analytics",
        "threat-modeling",
        "pen-testing",
    ],
}

# Cost-band thresholds: (person-weeks ceiling, label).  Last entry is the
# catch-all "over" band handled by the fallthrough in _cost_band().
_COST_BANDS: list[tuple[int, str]] = [
    (10, "$10k–$30k"),
    (25, "$30k–$75k"),
    (50, "$75k–$150k"),
    (100, "$150k–$350k"),
]
_COST_BAND_OVER = "$350k+"


# --------------------------------------------------------------------------- #
# Heuristic engine                                                             #
# --------------------------------------------------------------------------- #


def _infer_complexity(idea: Idea) -> int:
    """Map idea attributes to a complexity integer in [1, 5].

    Signals:
      - tech_stack breadth  (more distinct techs → higher integration cost)
      - mvp_scope verbosity (word count proxies feature breadth)
      - feasibility_score   (low feasibility → hard / novel / risky)

    Continuous score is normalised to [1, 5] via linear bucketing.
    """
    tech_score = min(len(idea.tech_stack) / 3.0, 4.0)
    scope_words = len((idea.mvp_scope or "").split())
    scope_score = min(scope_words / 40.0, 3.0)
    feasibility = max(0.0, min(1.0, idea.feasibility_score))
    difficulty_score = (1.0 - feasibility) * 2.0

    raw_score = tech_score + scope_score + difficulty_score  # range [0.0, 9.0]
    bucket = int(raw_score / 1.8) + 1  # 1.8 = 9 / 5 buckets
    return max(1, min(5, bucket))


def _cost_band(total_person_weeks: int) -> str:
    """Map total person-weeks to a rough USD cost band string."""
    for ceiling, label in _COST_BANDS:
        if total_person_weeks <= ceiling:
            return label
    return _COST_BAND_OVER


def _build_from_complexity(complexity: int, extra_skills: list[str]) -> dict[str, Any]:
    """Assemble the full estimate dict from a complexity rating."""
    roles = [dict(r) for r in _ROLE_TEMPLATES[complexity]]
    total_person_weeks = sum(r["count"] * r["weeks"] for r in roles)
    timeline_weeks = max(r["weeks"] for r in roles)
    base_skills = list(_TIER_SKILLS[complexity])
    base_lower = {s.lower() for s in base_skills}
    skills = base_skills + [t for t in extra_skills if t.lower() not in base_lower]
    return {
        "roles": roles,
        "total_person_weeks": total_person_weeks,
        "skills": skills,
        "cost_band": _cost_band(total_person_weeks),
        "complexity": complexity,
        "timeline_weeks": timeline_weeks,
    }


def _heuristic_estimate(idea: Idea) -> dict[str, Any]:
    """Deterministic heuristic estimate — always available, no LLM needed."""
    complexity = _infer_complexity(idea)
    return _build_from_complexity(complexity, list(idea.tech_stack))


# --------------------------------------------------------------------------- #
# LLM refinement                                                               #
# --------------------------------------------------------------------------- #


def _strip_codefence(raw: str) -> str:
    """Strip ```json ... ``` or ``` ... ``` fences from LLM output."""
    if "```json" in raw:
        return raw.split("```json", 1)[1].split("```", 1)[0].strip()
    if "```" in raw:
        return raw.split("```", 1)[1].split("```", 1)[0].strip()
    return raw


def _validate_estimate(data: dict[str, Any]) -> bool:
    """Minimal shape-check so malformed LLM responses don't propagate."""
    roles = data.get("roles")
    if not isinstance(roles, list) or not roles:
        return False
    for r in roles:
        if not all(k in r for k in ("role", "count", "weeks")):
            return False
    complexity = data.get("complexity")
    if not isinstance(complexity, int) or not (1 <= complexity <= 5):
        return False
    timeline = data.get("timeline_weeks")
    if not isinstance(timeline, int) or timeline < 1:
        return False
    return True


def _llm_estimate(idea: Idea, backend: Any, heuristic: dict[str, Any]) -> dict[str, Any] | None:
    """Ask the LLM for a refined build estimate.

    Returns the parsed dict on success, or None if the response is
    malformed (caller falls back to heuristic).
    """
    prompt = (
        "You are a senior engineering lead. Estimate the build cost for the "
        "software project below. Return ONLY valid JSON — no prose, no markdown "
        "fences — matching this schema exactly:\n"
        '{"roles":[{"role":"<title>","count":<int>,"weeks":<int>},...], '
        '"total_person_weeks":<int>,"skills":["..."],'
        '"cost_band":"<e.g. $75k-$150k>","complexity":<1-5 int>,'
        '"timeline_weeks":<int>}\n\n'
        f"## Idea: {idea.name}\n"
        f"**Tagline:** {idea.tagline}\n"
        f"**Description:** {idea.description}\n"
        f"**MVP scope:** {idea.mvp_scope}\n"
        f"**Tech stack:** {', '.join(idea.tech_stack)}\n"
        f"**Feasibility score:** {idea.feasibility_score}\n\n"
        f"Heuristic baseline (for reference): complexity={heuristic['complexity']}, "
        f"timeline_weeks={heuristic['timeline_weeks']}, "
        f"total_person_weeks={heuristic['total_person_weeks']}\n\n"
        "Return the JSON object only."
    )
    raw = (backend.call(prompt) or "").strip()
    cleaned = _strip_codefence(raw)
    try:
        data: dict[str, Any] = json.loads(cleaned)
    except Exception:
        logger.info("recruiter: LLM response not valid JSON; using heuristic")
        return None
    if not _validate_estimate(data):
        logger.info("recruiter: LLM response failed shape validation; using heuristic")
        return None
    return data


# --------------------------------------------------------------------------- #
# Public API                                                                   #
# --------------------------------------------------------------------------- #


def estimate_build(idea: Idea, *, backend: Any = None) -> dict[str, Any]:
    """Return a staffed build-cost estimate for *idea*.

    Return shape::

        {
          "roles": [{"role": str, "count": int, "weeks": int}, ...],
          "total_person_weeks": int,
          "skills": [str, ...],
          "cost_band": str,    # e.g. "$75k-$150k"
          "complexity": int,   # 1-5
          "timeline_weeks": int,
        }

    When *backend* is ``None`` the function auto-resolves via
    :func:`resolve_cheap_backend`.  If no backend resolves, or the LLM
    response is malformed, the deterministic heuristic is returned.
    """
    heuristic = _heuristic_estimate(idea)
    resolved = backend if backend is not None else resolve_cheap_backend()
    if resolved is None:
        return heuristic
    result = _llm_estimate(idea, resolved, heuristic)
    return result if result is not None else heuristic


def format_estimate_markdown(est: dict[str, Any]) -> str:
    """Render a build estimate as a compact markdown summary.

    Produces a header line with key metrics, a role/count/weeks table,
    and a skills list — all CSP-safe plain markdown.
    """
    lines: list[str] = [
        f"**Complexity:** {est['complexity']}/5  |  "
        f"**Timeline:** {est['timeline_weeks']} weeks  |  "
        f"**Cost band:** {est['cost_band']}",
        "",
        "| Role | Count | Weeks |",
        "|------|------:|------:|",
    ]
    for r in est["roles"]:
        lines.append(f"| {r['role']} | {r['count']} | {r['weeks']} |")
    lines += [
        "",
        f"**Total person-weeks:** {est['total_person_weeks']}",
        "",
        f"**Skills needed:** {', '.join(est['skills'])}",
    ]
    return "\n".join(lines)
