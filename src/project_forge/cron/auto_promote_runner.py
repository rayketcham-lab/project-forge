"""Auto-promote cadence — the money-flipper loop.

Each cycle picks the highest-fundability idea among the money-friendly
categories that hasn't been auto-promoted yet, flips it to 'approved',
creates a GitHub issue with the full MVP spec, and stamps
`auto_promoted_at` so subsequent runs skip it.

The gap this closes: v0.13 generates money-bot ideas and scores them
for fundability, but they sit at status='new' until a human clicks
approve. This cadence turns the engine from "idea factory" into
"weekly MVP candidate shipper."

Categories targeted (env-overridable via FORGE_PROMOTE_CATEGORIES):
  - AUTOMATION_INCOME   (money bots, content engines, lead-gen)
  - CREATOR_TOOLS       (creator economy SaaS)
  - CONSUMER_APP        (everyday-user products with paid tiers)
  - PRODUCTIVITY        (work tools with team-seat pricing)

Minimum fundability_score to be eligible: 0.55 (env: FORGE_PROMOTE_MIN_SCORE).
Target GH repo for the issue: settings.github_owner/settings.github_repo
or FORGE_PROMOTE_REPO for a dedicated money-bot board.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

from project_forge.config import settings
from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database

logger = logging.getLogger(__name__)


# Default money-friendly categories. Env override comma-separated values.
_DEFAULT_PROMOTE_CATEGORIES = (
    IdeaCategory.AUTOMATION_INCOME,
    IdeaCategory.CREATOR_TOOLS,
    IdeaCategory.CONSUMER_APP,
    IdeaCategory.PRODUCTIVITY,
)


def _promote_categories() -> list[IdeaCategory]:
    raw = os.environ.get("FORGE_PROMOTE_CATEGORIES", "")
    if not raw.strip():
        return list(_DEFAULT_PROMOTE_CATEGORIES)
    out: list[IdeaCategory] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            out.append(IdeaCategory(token))
        except ValueError:
            logger.warning("Unknown FORGE_PROMOTE_CATEGORIES entry: %r", token)
    return out or list(_DEFAULT_PROMOTE_CATEGORIES)


def _min_fundability() -> float:
    try:
        return float(os.environ.get("FORGE_PROMOTE_MIN_SCORE", "0.55"))
    except ValueError:
        return 0.55


def _promote_repo() -> str:
    explicit = os.environ.get("FORGE_PROMOTE_REPO", "").strip()
    if explicit:
        return explicit
    return f"{settings.github_owner}/{settings.github_repo}"


# --------------------------------------------------------------------------- #
# Picker                                                                      #
# --------------------------------------------------------------------------- #


async def pick_promotion_candidate(db: Database) -> Idea | None:
    """Top-fundability idea in money categories that hasn't been promoted.

    Returns None when no eligible candidate exists (all promoted, all below
    threshold, or none in the configured categories).
    """
    categories = _promote_categories()
    min_score = _min_fundability()

    placeholders = ",".join("?" * len(categories))
    params: list[Any] = [c.value for c in categories]
    params.append(min_score)
    cur = await db.db.execute(
        f"""
        SELECT id FROM ideas
        WHERE category IN ({placeholders})
          AND status = 'new'
          AND auto_promoted_at IS NULL
          AND fundability_score IS NOT NULL
          AND fundability_score >= ?
        ORDER BY fundability_score DESC, generated_at DESC
        LIMIT 1
        """,  # noqa: S608  -- placeholders is `?,?,?` from length, not user input
        params,
    )
    row = await cur.fetchone()
    if not row:
        return None
    return await db.get_idea(row["id"])


# --------------------------------------------------------------------------- #
# Issue body                                                                  #
# --------------------------------------------------------------------------- #


def build_issue_body(idea: Idea) -> str:
    """Markdown body for the auto-promote GitHub issue."""
    tech = ", ".join(idea.tech_stack) if idea.tech_stack else "(none specified)"
    score = (
        f"{idea.fundability_score:.2f}"
        if idea.fundability_score is not None
        else "n/a"
    )
    mode = idea.generation_mode or "template"
    return (
        f"# Auto-promoted by Project Forge\n\n"
        f"This idea was auto-promoted by the weekly money-flipper cadence "
        f"because it scored highest among monetizable categories.\n\n"
        f"- **Category:** `{idea.category.value}`\n"
        f"- **Fundability score:** {score}\n"
        f"- **Generation mode:** `{mode}`\n"
        f"- **Feasibility:** {idea.feasibility_score:.2f}\n\n"
        f"## Tagline\n{idea.tagline}\n\n"
        f"## Description\n{idea.description}\n\n"
        f"## Market\n{idea.market_analysis}\n\n"
        f"## MVP Scope\n{idea.mvp_scope}\n\n"
        f"## Tech Stack\n{tech}\n\n"
        f"---\n"
        f"_Stamped `auto_promoted_at`; the picker will skip this idea on "
        f"future cycles. To run the MVP build, scaffold this idea from the "
        f"dashboard or hand-off to the self-improve runner._\n"
    )


# --------------------------------------------------------------------------- #
# GitHub issue creation                                                       #
# --------------------------------------------------------------------------- #


def _create_promotion_issue(idea: Idea) -> str:
    """Create the GitHub issue. Raises RuntimeError on gh failure.

    Self-bootstraps the required labels so a fresh repo doesn't fail
    the first cycle. `create_label` is no-op when the label already
    exists, so this is cheap on every run.
    """
    from project_forge.scaffold.github import create_issue, create_label

    repo = _promote_repo()
    # Bootstrap labels (idempotent; fails-soft).
    try:
        create_label(repo, "auto-promoted", "0e8a16",
                     "Auto-promoted by the money-flipper cadence")
        create_label(repo, "money-bot", "fbca04",
                     "Money-making bot or monetization-focused project")
    except Exception:
        logger.warning("auto-promote: label bootstrap had a hiccup; continuing")

    title = f"[Money-Flipper] {idea.name}"
    body = build_issue_body(idea)
    return create_issue(
        repo,
        title,
        body,
        labels=["auto-promoted", "money-bot"],
    )


# --------------------------------------------------------------------------- #
# Cycle                                                                       #
# --------------------------------------------------------------------------- #


async def run_auto_promote_cycle(db: Database) -> dict[str, Any]:
    """One money-flipper cycle. Picks the best candidate, files a GH issue,
    flips status, stamps the timestamp.

    Order matters: issue creation runs FIRST. If it fails, the idea is
    left at 'new' so the next cycle retries. Half-promoted state (status
    flipped but no issue) is worse than no promotion at all.
    """
    candidate = await pick_promotion_candidate(db)
    if candidate is None:
        logger.info("auto-promote: no eligible candidate")
        return {"promoted": 0, "idea_id": None, "issue_url": None}

    logger.info(
        "auto-promote: candidate id=%s name=%r fundability=%.2f category=%s",
        candidate.id,
        candidate.name,
        candidate.fundability_score or 0.0,
        candidate.category.value,
    )

    try:
        issue_url = _create_promotion_issue(candidate)
    except Exception:
        logger.exception("auto-promote: issue creation failed; leaving idea at 'new'")
        return {"promoted": 0, "idea_id": candidate.id, "issue_url": None}

    candidate.status = "approved"
    candidate.github_issue_url = issue_url
    candidate.auto_promoted_at = datetime.now(UTC)
    await db.save_idea(candidate)

    logger.info(
        "auto-promote: promoted idea=%s issue=%s",
        candidate.id, issue_url,
    )
    return {
        "promoted": 1,
        "idea_id": candidate.id,
        "issue_url": issue_url,
        "name": candidate.name,
        "fundability_score": candidate.fundability_score,
    }
