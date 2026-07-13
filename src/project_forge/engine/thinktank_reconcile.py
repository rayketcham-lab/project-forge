"""Autonomously mark shipped Think Tank suggestions as implemented (#91).

The Think Tank never noticed when its suggestions shipped — a 2026-07-13
manual curation found 7 active items already implemented, some for weeks.
This reconciler cross-references active self-improvement ideas against
recent commit subjects and promotes confident matches to 'implemented'.

Commit subjects are the ONLY trusted signal. Closed promoted issues were
tried and refuted the same day: five '[Think Tank] Decompose X' issues
were closed as COMPLETED while the target files had grown to ~2x the
size the suggestions complained about — this repo's history contains
issues closed-complete without merged work.

Conservative by design: it never archives or rejects, and it requires
full containment of >=2 significant name tokens in a single commit
subject before touching a row.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from project_forge.models import IdeaCategory
from project_forge.storage.db import Database

logger = logging.getLogger(__name__)

# Generic words that carry no identity — a name reduced below two
# remaining tokens is too ambiguous to auto-match against commits.
_STOP_TOKENS = frozenset({"a", "an", "and", "add", "fix", "for", "in", "of", "on", "or", "the", "to", "wire", "with"})


def _tokens(text: str) -> set[str]:
    return {w for w in re.sub(r"[^a-z0-9 ]", " ", text.lower()).split() if w}


def _significant(text: str) -> set[str]:
    return _tokens(text) - _STOP_TOKENS


def _match(idea, subject_tokens: list[tuple[str, set[str]]]) -> str | None:
    """Return a provenance string when the idea's work has verifiably shipped."""
    name_toks = _significant(idea.name)
    if len(name_toks) < 2:
        return None
    for subject, toks in subject_tokens:
        if name_toks <= toks:
            return f"auto_reconcile: matched commit {subject!r}"
    return None


async def reconcile_thinktank(db: Database, commit_subjects: list[str]) -> dict:
    """Mark active self-improvement ideas as implemented when their work shipped.

    Returns {"scanned": N, "implemented": [idea ids]}.
    """
    ideas = await db.list_ideas(category=IdeaCategory.SELF_IMPROVEMENT, limit=1000)
    active = [i for i in ideas if i.status in ("new", "approved")]

    subject_tokens = [(s, _tokens(s)) for s in commit_subjects]

    implemented: list[str] = []
    now = datetime.now(UTC).isoformat()
    for idea in active:
        provenance = _match(idea, subject_tokens)
        if provenance is None:
            continue
        await db.db.execute(
            "UPDATE ideas SET status='implemented', archived_reason=?, archived_at=? WHERE id=?",
            (provenance, now, idea.id),
        )
        implemented.append(idea.id)
        logger.info("Reconciler: '%s' shipped — %s", idea.name, provenance)
    if implemented:
        await db.db.commit()

    return {"scanned": len(active), "implemented": implemented}
