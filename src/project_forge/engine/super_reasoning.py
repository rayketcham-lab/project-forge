"""Super-idea reasoning — replace slot-fill with Claude reasoning (Direction C).

Phase 6 (issue #59). The current super-idea name generation slot-fills
"{Keyword1} & {Keyword2} {Suffix}" templates from frequency-extracted
cluster keywords. This is statistical surface generation that produces
hollow, repetitive names ("Overlap & Visualizer Defense Suite").

This module provides reasoning-based naming:
1. cluster_signature(ideas): SHA256 hash of sorted member-idea IDs.
   New dedup anchor that does NOT rely on name regularity.
2. reason_cluster_name(ideas, llm_call): asks the LLM what capability
   the cluster's ideas are all dancing around but none capture, then
   uses that as the super idea's name.
3. find_super_by_signature(db, sig): look up an existing super by
   cluster signature (encoded in description as [CLUSTER:<sig>]).

Behind feature flag FORGE_SUPER_REASONING. Off by default until shadow
validation greenlights it as an improvement over the current slot-fill.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable
from typing import TYPE_CHECKING

from project_forge.models import Idea

if TYPE_CHECKING:
    from project_forge.storage.db import Database

logger = logging.getLogger(__name__)


def cluster_signature(ideas: list[Idea]) -> str:
    """Return a deterministic signature for a cluster's member-idea IDs.

    Sorted to be order-independent. SHA256 truncated to 16 hex chars
    (sufficient for collision avoidance among <10^9 supers).
    """
    if not ideas:
        return ""
    ids = sorted(i.id for i in ideas)
    h = hashlib.sha256("|".join(ids).encode("utf-8")).hexdigest()
    return h[:16]


_REASONING_PROMPT = """You are looking at a cluster of related project ideas. \
They all approach a similar capability gap from different angles, but no single \
one captures the unifying concept. Your job: name THAT specific capability \
gap — the thing they're all dancing around.

Cluster ideas:
{member_lines}

Constraints:
- Output a single short name (3-6 words) that names the unifying capability gap.
- Do NOT include a [SUPER] prefix — the caller adds that.
- Avoid template suffixes like "Defense Suite", "Operations Center", \
"Intelligence Center". Use a name that DESCRIBES the actual capability.
- Avoid generic words: tool, platform, system, suite.

Respond with ONLY valid JSON: {{"name": "Your Name Here"}}"""


def reason_cluster_name(
    ideas: list[Idea],
    llm_call: Callable[[str], str],
) -> str | None:
    """Ask the LLM to name the cluster's unifying capability gap.

    Args:
        ideas: cluster members
        llm_call: function taking a prompt string, returning Claude's response

    Returns the parsed name, or None if the response is unusable. Caller
    should fall back to slot-fill when None is returned.
    """
    member_lines = "\n".join(f"- {i.name}: {i.tagline}" for i in ideas[:10])
    prompt = _REASONING_PROMPT.format(member_lines=member_lines)

    try:
        raw = llm_call(prompt)
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM call failed in reason_cluster_name: %s", exc)
        return None

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.info("reason_cluster_name: LLM returned non-JSON, falling back")
        return None

    name = payload.get("name")
    if not name or not isinstance(name, str):
        return None
    # Strip [SUPER] prefix if LLM added it
    name = re.sub(r"^\s*\[SUPER\]\s*", "", name).strip()
    return name or None


_CLUSTER_TAG_RE = re.compile(r"\[CLUSTER:([0-9a-fA-F]+)\]")


def encode_cluster_tag(signature: str) -> str:
    """Render the cluster-signature tag for embedding in a super idea description."""
    return f"[CLUSTER:{signature}]"


def extract_cluster_signature(description: str) -> str | None:
    """Extract a cluster signature embedded in a super idea description."""
    if not description:
        return None
    m = _CLUSTER_TAG_RE.search(description)
    return m.group(1) if m else None


async def find_super_by_signature(db: Database, signature: str) -> Idea | None:
    """Look up an active super idea matching the given cluster signature."""
    if not signature:
        return None

    cursor = await db.db.execute(
        "SELECT * FROM ideas WHERE name LIKE '[SUPER]%' "
        "AND status NOT IN ('archived', 'rejected') "
        "AND description LIKE ?",
        (f"%[CLUSTER:{signature}]%",),
    )
    rows = await cursor.fetchall()
    if not rows:
        return None
    # Use the existing _row_to_idea helper if available
    return db._row_to_idea(rows[0])
