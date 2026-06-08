"""Approval-time think-tank sanity checker.

Runs when a human flips an idea to 'approved' and asks: does this thing
actually flow? Catches obvious coherence problems (mvp scope unrelated
to description, empty tech stack, fake-perfect feasibility, super-ideas
whose components have no shared theme) before the idea heads toward
scaffolding.

Non-blocking: the approval still completes. The check result is
persisted to `approval_checks` so the dashboard can surface a warning
banner. Re-running on the same idea overwrites the previous check.

Today's implementation is heuristic — no LLM call required, so it's
fast and works without a backend. The LLM version (deeper coherence
review) can be layered on later as a separate cadence.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from project_forge.models import Idea
from project_forge.storage.db import Database

_STOP = {
    "a", "an", "the", "and", "or", "for", "to", "with", "of", "in", "on",
    "via", "using", "tool", "system", "platform", "engine", "framework",
    "suite", "service", "based", "is", "be", "by", "across", "into",
    "phase", "step",
}
_COMPONENT_BULLET_RE = re.compile(r"^-\s*\*\*(?P<name>[^*]+)\*\*", re.MULTILINE)
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9]+")
_SPLIT_RE = re.compile(r"[\s/\-]+")
_SUFFIXES = ("ing", "ies", "ied", "ers", "ed", "es", "er", "s")


def _stem(word: str) -> str:
    """Strip common English plural/tense suffixes so "tracing" / "traces" /
    "traced" collapse to the same root for overlap comparison. Cheap; not
    linguistically rigorous."""
    for suf in _SUFFIXES:
        if len(word) > len(suf) + 2 and word.endswith(suf):
            return word[: -len(suf)]
    return word


def _significant_words(text: str) -> set[str]:
    out: set[str] = set()
    if not text:
        return out
    # Split on whitespace, slashes, hyphens — treat 'distributed-tracing' as two.
    for token in _SPLIT_RE.split(text):
        for m in _WORD_RE.findall(token):
            w = m.lower()
            if w in _STOP:
                continue
            out.add(_stem(w))
    return out


@dataclass
class ApprovalCheckResult:
    """One full pass over all check rules for one idea."""

    verdict: str  # 'pass' | 'warn' | 'fail'
    checks: list[dict[str, Any]] = field(default_factory=list)


def _aggregate_verdict(checks: list[dict[str, Any]]) -> str:
    if any(c["status"] == "fail" for c in checks):
        return "fail"
    if any(c["status"] == "warn" for c in checks):
        return "warn"
    return "pass"


def _check_tech_stack(idea: Idea) -> dict[str, Any]:
    if not idea.tech_stack:
        return {
            "name": "tech_stack_present",
            "status": "fail",
            "reason": "tech_stack is empty",
        }
    return {"name": "tech_stack_present", "status": "pass", "reason": ""}


def _check_score(idea: Idea) -> dict[str, Any]:
    score = idea.feasibility_score
    if score < 0.4:
        return {
            "name": "score_realistic",
            "status": "fail",
            "reason": f"feasibility_score={score:.2f} below approval floor 0.4",
        }
    if score >= 0.99:
        return {
            "name": "score_realistic",
            "status": "warn",
            "reason": f"feasibility_score={score:.2f} suspiciously perfect",
        }
    return {"name": "score_realistic", "status": "pass", "reason": ""}


def _check_scope_alignment(idea: Idea) -> dict[str, Any]:
    desc_words = _significant_words(idea.description)
    mvp_words = _significant_words(idea.mvp_scope)
    if not desc_words or not mvp_words:
        return {
            "name": "scope_alignment",
            "status": "warn",
            "reason": "description or mvp_scope is empty",
        }
    overlap = desc_words & mvp_words
    ratio = len(overlap) / min(len(desc_words), len(mvp_words))
    if ratio < 0.15:
        return {
            "name": "scope_alignment",
            "status": "fail",
            "reason": (
                f"description ↔ mvp_scope overlap={ratio:.0%} (<15%) — "
                "MVP may not match the described project"
            ),
        }
    if ratio < 0.30:
        return {
            "name": "scope_alignment",
            "status": "warn",
            "reason": f"description ↔ mvp_scope overlap={ratio:.0%} is thin",
        }
    return {"name": "scope_alignment", "status": "pass", "reason": ""}


def _extract_components(description: str) -> list[str]:
    return [m.group("name").strip() for m in _COMPONENT_BULLET_RE.finditer(description or "")]


def _check_super_components(idea: Idea) -> dict[str, Any] | None:
    if not idea.name.startswith("[SUPER]"):
        return None
    components = _extract_components(idea.description or "")
    if len(components) < 3:
        return {
            "name": "super_components_coherent",
            "status": "fail",
            "reason": f"super-idea has only {len(components)} component(s) (need ≥3)",
        }
    # Theme cohesion: at least one significant word should appear in 2+ component names.
    word_counts: dict[str, int] = {}
    for c in components:
        for w in _significant_words(c):
            word_counts[w] = word_counts.get(w, 0) + 1
    shared = [w for w, n in word_counts.items() if n >= 2]
    if not shared:
        return {
            "name": "super_components_coherent",
            "status": "warn",
            "reason": (
                "no significant word appears in 2+ component names — "
                "components may not share a theme"
            ),
        }
    return {"name": "super_components_coherent", "status": "pass", "reason": ""}


def validate_idea(idea: Idea) -> ApprovalCheckResult:
    """Run all applicable checks for `idea` and aggregate the verdict."""
    checks = [
        _check_tech_stack(idea),
        _check_score(idea),
        _check_scope_alignment(idea),
    ]
    super_check = _check_super_components(idea)
    if super_check is not None:
        checks.append(super_check)
    return ApprovalCheckResult(verdict=_aggregate_verdict(checks), checks=checks)


# --------------------------------------------------------------------------- #
# Persistence                                                                 #
# --------------------------------------------------------------------------- #


_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS approval_checks (
    id TEXT PRIMARY KEY,
    idea_id TEXT NOT NULL,
    verdict TEXT NOT NULL,
    checks TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""
_CREATE_IDEA_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_approval_checks_idea ON approval_checks(idea_id);"
)


async def _ensure_table(db: Database) -> None:
    await db.db.execute(_CREATE_TABLE_SQL)
    await db.db.execute(_CREATE_IDEA_INDEX_SQL)
    await db.db.commit()


async def save_approval_check(
    db: Database,
    idea_id: str,
    result: ApprovalCheckResult,
) -> str:
    """Persist a check. Returns the row id. Idempotent on idea_id —
    rerunning overwrites the previous row."""
    await _ensure_table(db)
    await db.db.execute(
        "DELETE FROM approval_checks WHERE idea_id = ?",
        (idea_id,),
    )
    row_id = uuid.uuid4().hex[:12]
    await db.db.execute(
        "INSERT INTO approval_checks (id, idea_id, verdict, checks, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            row_id,
            idea_id,
            result.verdict,
            json.dumps(result.checks),
            datetime.now(UTC).isoformat(),
        ),
    )
    await db.db.commit()
    return row_id


async def get_approval_check(db: Database, idea_id: str) -> dict[str, Any] | None:
    """Return the latest stored check for `idea_id`, or None."""
    await _ensure_table(db)
    cur = await db.db.execute(
        "SELECT id, verdict, checks, created_at FROM approval_checks "
        "WHERE idea_id = ? ORDER BY created_at DESC LIMIT 1",
        (idea_id,),
    )
    row = await cur.fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "idea_id": idea_id,
        "verdict": row["verdict"],
        "checks": json.loads(row["checks"]),
        "created_at": row["created_at"],
    }
