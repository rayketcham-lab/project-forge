"""Verdict meta-audit cadence — 'who watches the watcher?'.

Premise: challenge / review verdicts come from an LLM. If the LLM is
confidently wrong, the engine is confidently wrong, and the dashboard's
'kill / strengthen / pivot' signals stop being trustworthy. This runner
takes a small random sample of recent verdicts each cycle, re-runs each
one with a *different tone* (skeptical ↔ curious), and persists the
delta as a `verdict_audits` row.

Why tone swap (not model swap): the same backend re-asked with a
different framing surfaces verdict instability cheaply. Same answer to
both tones = high confidence in the original verdict. Wildly different =
flag for human review.

Today's scope:
- Audits challenges only. Review verdicts can be added similarly later.
- Heuristic re-evaluation when no LLM backend is reachable so the
  cadence never blows up; the audit row carries `tone='heuristic'` so
  consumers can ignore it.

Schema (created on first run, idempotent):
    verdict_audits(
      id, source_type, source_id, idea_id,
      original_verdict, audit_verdict,
      divergence, audit_notes, created_at
    )
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from project_forge.engine.llm_backend import resolve_backend
from project_forge.storage.db import Database

logger = logging.getLogger(__name__)


# Ordered verdict spectrum (negative = kill / narrow, positive = expand /
# strengthen). Divergence between two verdicts is the normalised L1
# distance on this scale.
_VERDICT_AXIS = {
    "kill": -2.0,
    "narrow": -1.0,
    "pivot": -0.5,
    "no_change": 0.0,
    "expand": 1.0,
    "strengthen": 2.0,
}
_VERDICT_RANGE = max(_VERDICT_AXIS.values()) - min(_VERDICT_AXIS.values())


def verdict_divergence(a: str, b: str) -> float:
    """Return a divergence score in [0.0, 1.0] for two verdict labels."""
    av = _VERDICT_AXIS.get(a, 0.0)
    bv = _VERDICT_AXIS.get(b, 0.0)
    return abs(av - bv) / _VERDICT_RANGE


# --------------------------------------------------------------------------- #
# Schema management                                                           #
# --------------------------------------------------------------------------- #


_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS verdict_audits (
    id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    idea_id TEXT NOT NULL,
    original_verdict TEXT NOT NULL,
    audit_verdict TEXT NOT NULL,
    divergence REAL NOT NULL,
    audit_notes TEXT,
    created_at TEXT NOT NULL
);
"""
_INDEX_SOURCE = "CREATE INDEX IF NOT EXISTS idx_audits_source ON verdict_audits(source_type, source_id);"
_INDEX_IDEA = "CREATE INDEX IF NOT EXISTS idx_audits_idea ON verdict_audits(idea_id);"


async def _ensure_table(db: Database) -> None:
    await db.db.execute(_CREATE_TABLE_SQL)
    await db.db.execute(_INDEX_SOURCE)
    await db.db.execute(_INDEX_IDEA)
    await db.db.commit()


# --------------------------------------------------------------------------- #
# Re-evaluation                                                               #
# --------------------------------------------------------------------------- #


@dataclass
class _AuditOutcome:
    audit_verdict: str
    divergence: float
    audit_notes: str


def _flip_tone(tone: str) -> str:
    return {
        "skeptical": "curious",
        "curious": "skeptical",
        "adversarial": "curious",
    }.get(tone or "", "curious")


async def _re_evaluate_challenge(
    idea,
    question: str,
    original_verdict: str,
    original_tone: str,
) -> dict[str, Any]:
    """Re-run the challenge with a flipped tone via the resolved backend.

    Returns {response, verdict, confidence, audit_notes}. Falls back to a
    neutral heuristic when no backend is reachable so the cadence keeps
    producing audit rows (they're tagged so consumers can ignore them).
    """
    backend = resolve_backend()
    new_tone = _flip_tone(original_tone)
    if backend is None:
        return {
            "response": "(no backend; audit skipped)",
            "verdict": original_verdict,
            "confidence": 0.0,
            "audit_notes": f"no_backend; tone={new_tone}",
        }
    prompt = (
        "Second-opinion review. Respond ONLY with valid JSON.\n\n"
        f"## Idea: {idea.name}\n"
        f"**Tagline:** {idea.tagline}\n"
        f"**Description:** {idea.description}\n\n"
        f"## Original verdict on this idea: {original_verdict}\n"
        f"## Original challenge question: {question}\n\n"
        f"Adopt a {new_tone} tone (the original was {original_tone or 'unknown'}). "
        "Independently decide your verdict. JSON:\n"
        "{\n"
        '  "verdict": "strengthen|pivot|narrow|expand|kill|no_change",\n'
        '  "confidence": 0.0-1.0,\n'
        '  "notes": "1-2 sentence explanation"\n'
        "}\n"
    )
    raw = (await asyncio.to_thread(backend.call, prompt) or "").strip()
    if "```json" in raw:
        raw = raw.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in raw:
        raw = raw.split("```", 1)[1].split("```", 1)[0].strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {"verdict": original_verdict, "confidence": 0.0, "notes": "parse_error"}
    return {
        "response": raw,
        "verdict": data.get("verdict", original_verdict),
        "confidence": float(data.get("confidence", 0.0)),
        "audit_notes": f"tone={new_tone}; {data.get('notes', '')}",
    }


# --------------------------------------------------------------------------- #
# Sampling + persistence                                                      #
# --------------------------------------------------------------------------- #


async def _unaudited_challenges(db: Database) -> list[dict[str, Any]]:
    """Pull challenges that don't yet have a verdict_audits row."""
    await _ensure_table(db)
    cur = await db.db.execute(
        """
        SELECT c.id, c.idea_id, c.question, c.verdict, c.tone
        FROM challenges c
        LEFT JOIN verdict_audits a
          ON a.source_type = 'challenge' AND a.source_id = c.id
        WHERE a.id IS NULL
        ORDER BY c.created_at DESC
        """
    )
    return [
        {
            "id": r["id"],
            "idea_id": r["idea_id"],
            "question": r["question"],
            "verdict": r["verdict"],
            "tone": r["tone"],
        }
        for r in await cur.fetchall()
    ]


async def _persist_audit(
    db: Database,
    *,
    source_type: str,
    source_id: str,
    idea_id: str,
    original_verdict: str,
    audit_verdict: str,
    divergence: float,
    notes: str,
) -> None:
    await _ensure_table(db)
    await db.db.execute(
        "INSERT INTO verdict_audits "
        "(id, source_type, source_id, idea_id, original_verdict, audit_verdict, "
        " divergence, audit_notes, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            uuid.uuid4().hex[:12],
            source_type,
            source_id,
            idea_id,
            original_verdict,
            audit_verdict,
            divergence,
            notes,
            datetime.now(UTC).isoformat(),
        ),
    )
    await db.db.commit()


async def run_verdict_audit_cycle(
    db: Database,
    *,
    sample_rate: float = 0.1,
    divergence_threshold: float = 0.25,
    seed: int | None = None,
) -> dict[str, Any]:
    """One audit cycle.

    `sample_rate` ∈ (0, 1] is the fraction of un-audited challenges to
    re-run. `divergence_threshold` is what counts as a flagged
    disagreement in the summary count. `seed` makes sampling
    deterministic for tests.
    """
    candidates = await _unaudited_challenges(db)
    if not candidates:
        return {"audited": 0, "divergences": 0, "results": []}

    rng = random.Random(seed) if seed is not None else random.Random()
    target_count = max(1, int(len(candidates) * sample_rate)) if sample_rate > 0 else 0
    sample = rng.sample(candidates, min(target_count, len(candidates)))

    results: list[dict[str, Any]] = []
    divergences = 0
    for cand in sample:
        idea = await db.get_idea(cand["idea_id"])
        if idea is None:
            continue
        try:
            outcome = await _re_evaluate_challenge(
                idea,
                cand["question"],
                cand["verdict"],
                cand["tone"],
            )
        except Exception:
            logger.exception("audit re-eval failed for challenge %s", cand["id"])
            continue
        d = verdict_divergence(cand["verdict"], outcome["verdict"])
        await _persist_audit(
            db,
            source_type="challenge",
            source_id=cand["id"],
            idea_id=cand["idea_id"],
            original_verdict=cand["verdict"],
            audit_verdict=outcome["verdict"],
            divergence=d,
            notes=outcome.get("audit_notes", ""),
        )
        if d >= divergence_threshold:
            divergences += 1
        results.append(
            {
                "challenge_id": cand["id"],
                "idea_id": cand["idea_id"],
                "original": cand["verdict"],
                "audit": outcome["verdict"],
                "divergence": d,
            }
        )

    return {
        "audited": len(results),
        "divergences": divergences,
        "results": results,
    }
