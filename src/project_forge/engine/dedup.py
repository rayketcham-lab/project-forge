"""Fuzzy deduplication for self-improvement ideas.

Uses token-set overlap on normalized taglines to detect near-duplicate
ideas like "dashboard UX improvements — tailored for developer experience"
vs "dashboard UX improvements — tailored for test engineering".
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

# Strips "for Healthcare", "for Defense/DoD", "for Cloud Environments", etc.
_FOR_VERTICAL_RE = re.compile(r"\s+for\s+[\w/&\-]+(\s+[\w/&\-]+){0,3}\s*$", re.IGNORECASE)

if TYPE_CHECKING:
    from project_forge.models import Idea
    from project_forge.storage.db import Database

logger = logging.getLogger(__name__)

# Similarity threshold: ideas above this score are considered duplicates.
# Tightened 0.7 → 0.6 in #71 — retroactive siphon revealed many pairs at
# 0.6-0.7 that are clearly paraphrases. Going-forward, reject them at
# INSERT time so the historical fat-trim doesn't have to repeat.
SIMILARITY_THRESHOLD = 0.6

# Additional INSERT-time gates (added after the May 2026 corpus inspection
# showed the corpus regrowing dupes the moment generation resumed).
NAME_JACCARD_THRESHOLD = 0.55  # token-Jaccard on names within a category
VERTICAL_CAP = 2  # max active ideas per stripped "X for {vertical}" concept
SUPER_COMPONENT_OVERLAP_MIN = 3  # shared atoms before two supers are dups

# Matches the trailing "for <vertical>" suffix (one to four words) so we can
# strip it and detect the concept stem ("Pqc Tracker for Healthcare" → "Pqc
# Tracker"). Keep in sync with siphon._FOR_VERTICAL_RE.
_NAME_VERTICAL_RE = re.compile(
    r"\s+for\s+[\w/&\-]+(\s+[\w/&\-]+){0,3}\s*$",
    re.IGNORECASE,
)

# Component bullet extractor for super-idea descriptions. The synthesiser
# emits each component as `- **Name**: blurb…`. Keep in sync with
# siphon._COMPONENT_BULLET_RE.
_COMPONENT_BULLET_RE = re.compile(r"^-\s*\*\*(?P<name>[^*]+)\*\*", re.MULTILINE)


def _name_token_jaccard(a: str, b: str) -> float:
    """Token-Jaccard similarity on two names after vertical-strip + lowercase."""
    ta = set(_normalize(a).split())
    tb = set(_normalize(b).split())
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _strip_vertical_name(name: str) -> str | None:
    """Return concept stem of 'X for {vertical}', or None if no match."""
    m = _NAME_VERTICAL_RE.search(name)
    if not m:
        return None
    stem = name[: m.start()].strip()
    return _normalize(stem) or None


def _extract_super_components(description: str) -> set[str]:
    """Pull normalised component names out of a super-idea description body."""
    out: set[str] = set()
    for m in _COMPONENT_BULLET_RE.finditer(description):
        n = _normalize(m.group("name"))
        if n:
            out.add(n)
    return out


def _normalize(text: str) -> str:
    """Strip Claude generation suffix artifacts and normalize."""
    # Remove everything after em dash, en dash, or double hyphen
    for sep in ("\u2014", "\u2013", "--"):
        if sep in text:
            text = text[: text.index(sep)]
    # Strip "for Healthcare / for Defense/DoD / for Cloud Environments" tail
    text = _FOR_VERTICAL_RE.sub("", text)
    return text.strip().lower()


def _tokenize(text: str) -> set[str]:
    """Normalize and tokenize a tagline into a set of lowercase words."""
    return set(_normalize(text).split())


def tagline_similarity(a: str, b: str) -> float:
    """Return 0.0–1.0 similarity score between two taglines using token overlap.

    Uses Jaccard-like similarity: |intersection| / |union|.
    Returns 1.0 for identical (including both empty), 0.0 for no overlap.
    """
    tokens_a = _tokenize(a)
    tokens_b = _tokenize(b)

    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0

    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


async def should_accept(idea: Idea, db: Database) -> tuple[bool, str | None]:
    """Check if an idea should be accepted or rejected as a duplicate.

    Returns (True, None) if the idea is unique enough to store,
    or (False, reason) if it should be filtered out.
    """
    # Check 1: content hash duplicate
    content_hash = getattr(idea, "content_hash", None)
    if content_hash:
        cursor = await db.db.execute("SELECT id FROM ideas WHERE content_hash = ?", (content_hash,))
        existing = await cursor.fetchone()
        if existing:
            return False, f"duplicate:content_hash (matches {existing[0]})"

    # Check 2: super idea dedup (cross-category)
    if idea.name.startswith("[SUPER]"):
        # 2a — exact-base-name match
        candidate_base = _super_base_name(idea.name)
        cursor = await db.db.execute(
            "SELECT id, name, description FROM ideas "
            "WHERE name LIKE '[SUPER]%' AND status NOT IN ('rejected', 'archived')",
        )
        rows = await cursor.fetchall()
        for row in rows:
            existing_base = _super_base_name(row[1])
            if candidate_base == existing_base:
                return False, f"duplicate:super_base_name (matches {row[0]})"

        # 2b — component-overlap: reject if the candidate shares ≥ N atoms
        # with any existing super. Catches the "Drift Tracker / Drift
        # Tracking / Compliance Verification" remake pattern where the
        # base names differ but the underlying atoms are the same.
        cand_components = _extract_super_components(idea.description or "")
        if len(cand_components) >= SUPER_COMPONENT_OVERLAP_MIN:
            for row in rows:
                existing_components = _extract_super_components(row[2] or "")
                shared = len(cand_components & existing_components)
                if shared >= SUPER_COMPONENT_OVERLAP_MIN:
                    return False, (
                        f"duplicate:super_overlap:{shared} (shares atoms with {row[0]})"
                    )
        return True, None

    # Check 3: vertical-cap. Done BEFORE the tagline / name-similarity loop
    # so a vertical clone whose stem already has ≥ cap siblings is rejected
    # by the cap (clean reason), and so the cap rules — not name-sim — own
    # the "X for V₁" vs "X for V₂" comparison.
    cand_stem = _strip_vertical_name(idea.name)
    if cand_stem is not None:
        cursor = await db.db.execute(
            "SELECT id, name FROM ideas "
            "WHERE lower(name) LIKE '% for %' "
            "AND status NOT IN ('archived', 'rejected')",
        )
        same_concept = 0
        for row in await cursor.fetchall():
            other_stem = _strip_vertical_name(row[1])
            if other_stem == cand_stem:
                same_concept += 1
        if same_concept >= VERTICAL_CAP:
            return False, (
                f"duplicate:vertical_cap:{same_concept}>={VERTICAL_CAP} "
                f"(concept stem '{cand_stem}')"
            )

    # Check 4: fuzzy tagline dedup + name-token Jaccard (regular ideas only).
    # Skip rows that belong to the same vertical-clone family as the candidate;
    # the cap above is the authority for that family.
    cursor = await db.db.execute(
        "SELECT id, name, tagline FROM ideas "
        "WHERE category = ? AND status != 'rejected'",
        (idea.category.value,),
    )
    rows = await cursor.fetchall()
    for row in rows:
        existing_id, existing_name, existing_tagline = row[0], row[1], row[2]
        if cand_stem is not None and _strip_vertical_name(existing_name) == cand_stem:
            # Same vertical family — already passed the cap.
            continue
        score = tagline_similarity(idea.tagline, existing_tagline)
        if score >= SIMILARITY_THRESHOLD:
            return False, f"duplicate:tagline_similarity:{score:.2f} (similar to {existing_id})"
        n_score = _name_token_jaccard(idea.name, existing_name)
        if n_score >= NAME_JACCARD_THRESHOLD:
            return False, f"duplicate:name_similarity:{n_score:.2f} (similar to {existing_id})"

    return True, None


# Keep in sync with _SYNTHESIS_SUFFIXES in engine/super_ideas.py
_SUPER_SYNTHESIS_SUFFIXES = frozenset({
    "intelligence center", "operations center", "defense suite",
    "governance engine", "observatory", "command center",
    "analysis hub", "enforcement suite", "discovery engine",
    "lifecycle platform", "security intelligence", "automation hub",
})


def _super_base_name(full_name: str) -> str:
    """Extract base name from a super idea name for dedup comparison.

    "[SUPER] Threat Engine (Attack & Defense)" → "threat engine"
    "[SUPER] Well Known Defense Suite"         → "well known"
    "[SUPER] Data-Cardinality Operations Center" → "data cardinality"
    """
    raw = full_name.replace("[SUPER] ", "")
    # Strip parenthetical suffixes first
    base = re.sub(r"\s*\([^)]+\)\s*$", "", raw).strip()
    # Strip synthesis suffixes (longest match wins — check all)
    base_lower = base.lower()
    for suffix in _SUPER_SYNTHESIS_SUFFIXES:
        if base_lower.endswith(suffix):
            base = base[: -len(suffix)].strip()
            break
    # Normalize separators: hyphens and ampersands → spaces
    base = base.replace("-", " ").replace("&", " ")
    # Collapse multiple spaces
    base = re.sub(r"\s+", " ", base).strip()
    return base.lower()


async def filter_and_save(idea: Idea, db: Database) -> tuple[Idea, bool, str | None]:
    """Run dedup gate, log filtered ideas, and save if accepted.

    Returns (idea, accepted, reason).
    """
    from project_forge.models import FilteredIdea

    accepted, reason = await should_accept(idea, db)
    if not accepted:
        # Extract similar_to_id from reason if present
        similar_to_id = None
        if reason and "(matches " in reason:
            similar_to_id = reason.split("(matches ")[-1].rstrip(")")
        elif reason and "(similar to " in reason:
            similar_to_id = reason.split("(similar to ")[-1].rstrip(")")

        fi = FilteredIdea(
            idea_name=idea.name,
            idea_tagline=idea.tagline,
            idea_category=idea.category,
            filter_reason=reason or "duplicate:unknown",
            original_idea_json=json.dumps({"name": idea.name, "tagline": idea.tagline}),
            similar_to_id=similar_to_id,
        )
        await db.save_filtered_idea(fi)
        logger.info("Filtered idea '%s': %s", idea.name, reason)
        return idea, False, reason

    await db.save_idea(idea)
    return idea, True, None
